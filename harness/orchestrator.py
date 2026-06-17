"""Harness orchestration loop (M1.4-5 → M2.2a). 驱动一个 session 到静止。

事件驱动:每轮扫 Session 找"可行动状态",调对应组件,循环到静止(quiescent)。
单卡流(决策 B):guide 分解 -> 派 builder 执行 -> 派 verifier 机器验证 -> 派 tailor 代码审查
-> Guide 仲裁(pass: merge 终态 / reject: hitl 兜底)。多卡 test-first 编排留 M2.4+。

M2.2a(decision 甲):验收链从 M1 的 decide_verdict(Guide) 升级为 verify(机器) + tailor review
(代码,隔离 context)两道独立闸 + Guide 仲裁 merge。decide_verdict 保留为无 tailor 的 M1
简化路径(orchestrator 不再调用)。reject -> 返工循环留 M2.2b(当前暂 hitl 兜底)。

组件归属:
- LLM:guide_step(分解) / execute_npc(builder 迭代) / review_task(tailor 审查)
- 确定性:verify_task / wake

guide_step 是纯函数(返回事件、不写 store),编排器负责把其返回事件落盘(桥接两种风格)。
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from harness.guide.step import guide_step
from harness.sandbox.executor import execute_npc
from harness.sandbox.subprocess_executor import run_npc_subprocess
from harness.sandbox.verify_executor import verify_task
from harness.sandbox.review_executor import review_task
from harness.sandbox.worktree import (
    instance_to_slug, branch_name, create_worktree,
    merge_to_main, add_verify_worktree, remove_worktree_path,
)
from harness.wake import wake

# role -> instance 映射。verifier/reviewer M2 仍单实例;builder 多卡时按卡序分配多实例(见
# _builder_instance),保证 test-first 测试作者≠实现者(ADR-004 / ADR-015)。
ROLE_INSTANCE = {"builder": "merchant#1", "verifier": "blaster#1", "reviewer": "tailor#1"}


def _builder_delegates(events):
    return sorted(
        [e for e in events if e["type"] == "guide_delegate"
         and e["payload"]["task_card"].get("assignee_role") == "builder"],
        key=lambda e: e["event_id"],
    )


def _builder_instance(events, delegate):
    """按 builder 卡出现顺序稳定分配实例:第 i 张 → merchant#(i+1)。
    test-first 中测试卡(第1张)→ merchant#1、实现卡(第2张)→ merchant#2,作者≠实现者。"""
    bds = _builder_delegates(events)
    idx = next(i for i, e in enumerate(bds) if e["event_id"] == delegate["event_id"])
    return f"merchant#{idx + 1}"


def _deps_satisfied(events, task_card):
    """task_card.depends_on 的每个 task_id 都已 merge(完成)。"""
    merged = {e["payload"]["task_id"] for e in events if e["type"] == "merge"}
    return all(dep in merged for dep in task_card.get("depends_on", []))


def _ready_builder_dispatches(events):
    """所有"可派发"的 builder 卡:未派(无引用它的 builder guide_assign)+ depends_on 已满足。
    与 _decide stage 2 同判据,但收集全部(供并行批,ADR-018)。"""
    assigns = [e for e in events if e["type"] == "guide_assign"]
    ready = []
    for d in _builder_delegates(events):
        inst = _builder_instance(events, d)
        assigned = any(
            a["payload"].get("task_card_event_id") == d["event_id"]
            and a["payload"].get("assignee_instance") == inst
            for a in assigns
        )
        if assigned or not _deps_satisfied(events, d["payload"]["task_card"]):
            continue
        ready.append(d)
    return ready


def _dispatch_builders_parallel(
    ready, store, events, *, repo_root, worktrees_base,
    builder_scripted_actions, max_concurrent_agents,
):
    """并行批派发(ADR-018):顺序写 guide_assign + 顺序预建 worktree(规避 git add 竞争),
    再 ThreadPoolExecutor 并发 spawn 子进程执行,等全部完成。"""
    jobs = []
    for d in ready:
        card = d["payload"]["task_card"]
        inst = _builder_instance(events, d)
        ga = store.append_event(
            agent="guide", type="guide_assign", parent_event_id=d["event_id"],
            session_id=d["session_id"],
            payload={"task_card_event_id": d["event_id"], "assignee_instance": inst},
        )
        # 顺序预建 worktree(从 main 切);子进程 reuse=True,避开并发 git worktree add 锁竞争
        if not (worktrees_base / instance_to_slug(inst)).is_dir():
            create_worktree(inst, repo_root=repo_root, base_dir=worktrees_base)
        scripted = (builder_scripted_actions or {}).get(card["task_id"])
        jobs.append((inst, card, ga["event_id"], scripted))

    with ThreadPoolExecutor(max_workers=max_concurrent_agents) as ex:
        futures = [
            ex.submit(
                run_npc_subprocess,
                npc_instance_id=inst, task_card=card,
                db_path=str(store.db_path), session_id=store.session_id,
                guide_assign_event_id=eid,
                repo_root=str(repo_root), worktrees_base=str(worktrees_base),
                scripted_actions=scripted, reuse_worktree=True,
            )
            for (inst, card, eid, scripted) in jobs
        ]
        for f in as_completed(futures):
            f.result()  # 传播子进程异常(NpcSubprocessError),不吞


def _append_guide_event(store, ev):
    """把 guide_step 返回的事件(纯函数,id 预分配)落进 store。

    guide_step 内部用 _next_event_id(session_events) 算起始 id,与 store 的自增同构,
    故按返回顺序 append、沿用其 parent_event_id,链路保持一致。
    """
    return store.append_event(
        agent=ev["agent"], type=ev["type"], payload=ev["payload"],
        parent_event_id=ev["parent_event_id"], session_id=ev["session_id"], ts=ev.get("ts"),
    )


def _delegate_for_task(events, task_id):
    for e in events:
        if e["type"] == "guide_delegate" and e["payload"]["task_card"]["task_id"] == task_id:
            return e
    return None


def _has_later(events, after_eid, types, *, task_id=None):
    """是否存在 event_id > after_eid、类型 ∈ types(可选 payload.task_id 匹配)的事件。"""
    for e in events:
        if e["event_id"] <= after_eid or e["type"] not in types:
            continue
        if task_id is not None and e["payload"].get("task_id") != task_id:
            continue
        return True
    return False


def _decide(events, max_rework):
    """返回下一个可行动 (kind, ctx) 或 None(静止)。round-aware:用"无更晚配对事件"判断,
    支持同一 task 多轮(返工)而不死循环。"""
    has_cmd = any(e["type"] == "user_command" for e in events)
    # 1. user_command 未分解(且 guide 未走 hitl 兜底)
    if has_cmd and not any(e["type"] in ("guide_delegate", "hitl_request") for e in events):
        return ("guide_decompose", next(e for e in events if e["type"] == "user_command"))

    assigns = [e for e in events if e["type"] == "guide_assign"]
    builder_insts = {f"merchant#{i + 1}" for i in range(len(_builder_delegates(events)))}

    # 2. builder 卡首次派发:无引用它的 builder guide_assign,且 depends_on 已满足(test-first
    #    实现卡在测试卡 merge 后才派)。返工的重派走 stage 5。
    for d in _builder_delegates(events):
        inst = _builder_instance(events, d)
        assigned = any(
            a["payload"].get("task_card_event_id") == d["event_id"]
            and a["payload"].get("assignee_instance") == inst
            for a in assigns
        )
        if assigned:
            continue
        if not _deps_satisfied(events, d["payload"]["task_card"]):
            continue  # 前置卡未完成,暂不派(依赖排序)
        return ("dispatch_builder", d)

    # 2.5 崩溃续作(M2.3):builder 已派但构建未完成(其后无 review_request)→ 续作该 builder。
    for a in assigns:
        if a["payload"].get("assignee_instance") not in builder_insts:
            continue
        tce = a["payload"].get("task_card_event_id")
        d = next((e for e in events
                  if e["type"] == "guide_delegate" and e["event_id"] == tce), None)
        if d is None:
            continue
        tid = d["payload"]["task_card"]["task_id"]
        if not _has_later(events, a["event_id"], {"review_request"}, task_id=tid):
            return ("finish_build", a)

    # 3. review_request 之后无 verify_run → 派验证(round-aware:每轮的最新 review_request)
    for rr in events:
        if rr["type"] == "review_request":
            tid = rr["payload"]["task_id"]
            if not _has_later(events, rr["event_id"], {"verify_run"}, task_id=tid):
                return ("dispatch_verifier", rr)

    # 4. verify_run 之后无 review_verdict → 派 tailor 审查(decision 甲)
    for vr in events:
        if vr["type"] == "verify_run":
            tid = vr["payload"]["task_id"]
            if not _has_later(events, vr["event_id"], {"review_verdict"}, task_id=tid):
                return ("dispatch_reviewer", vr)

    # 5. review_verdict 仲裁
    for rv in events:
        if rv["type"] != "review_verdict":
            continue
        tid = rv["payload"]["task_id"]
        eid = rv["event_id"]
        if rv["payload"]["verdict"] == "pass":
            if not _has_later(events, eid, {"merge"}, task_id=tid):
                return ("arbitrate_pass", rv)
        else:  # reject
            # 已处理:此 reject 之后已有"该 task 的 builder 重派"(返工)或 hitl(上抬)
            d = _delegate_for_task(events, tid)
            builder_redispatched = d is not None and any(
                a["payload"].get("task_card_event_id") == d["event_id"]
                and a["payload"].get("assignee_instance") in builder_insts
                and a["event_id"] > eid
                for a in assigns
            )
            escalated = _has_later(events, eid, {"hitl_request"}, task_id=tid)
            if builder_redispatched or escalated:
                continue
            reject_count = sum(
                1 for e in events
                if e["type"] == "review_verdict" and e["payload"]["task_id"] == tid
                and e["payload"]["verdict"] == "reject" and e["event_id"] <= eid
            )
            if reject_count <= max_rework:
                return ("rework", rv)
            return ("escalate_reject", rv)

    return None


def _run_builder(
    npc_in_subprocess, *, inst, card, store, trigger_eid,
    repo_root, worktrees_base, llm_client, scripted, reuse, rework_notes=None,
):
    """派 builder 执行:opt-in 子进程(ADR-017)或进程内(默认)。两路语义等价——
    子进程端事件经同一 SQLite 旁路落盘,故对编排器后续读取透明。"""
    if npc_in_subprocess:
        run_npc_subprocess(
            npc_instance_id=inst, task_card=card,
            db_path=str(store.db_path), session_id=store.session_id,
            guide_assign_event_id=trigger_eid,
            repo_root=str(repo_root), worktrees_base=str(worktrees_base),
            scripted_actions=scripted, reuse_worktree=reuse, rework_notes=rework_notes,
        )
    else:
        execute_npc(
            inst, card, store, trigger_eid,
            repo_root=repo_root, worktrees_base=worktrees_base,
            llm_client=llm_client, scripted_actions=scripted,
            reuse_worktree=reuse, rework_notes=rework_notes,
        )


def advance(
    session_store,
    *,
    llm_client=None,
    repo_root: Path = Path("."),
    worktrees_base: Path = Path("data/worktrees"),
    builder_scripted_actions=None,
    max_steps: int = 50,
    max_rework: int = 2,
    npc_in_subprocess: bool = False,
    max_concurrent_agents: int = 10,
) -> dict:
    """驱动 session 到静止,返回 wake() 最终状态视图。

    builder_scripted_actions: {task_id: [actions]} 测试注入(离线确定性);None=live LLM builder。
    max_rework: review reject 后重派 builder 的上限(M2.2b);超限 → hitl 兜底。
    """
    for _ in range(max_steps):
        events = session_store.query_session()

        # 并行快路(ADR-018):≥2 张独立 builder 卡同时 ready → 并发批派发(子进程);
        # 仅 1 张 ready 落到下方顺序路径(尊重 npc_in_subprocess)。test-first 顺序多卡
        # (impl depends_on tests)始终只有 1 张 ready,故不受影响。
        ready = _ready_builder_dispatches(events)
        if len(ready) >= 2:
            _dispatch_builders_parallel(
                ready, session_store, events,
                repo_root=repo_root, worktrees_base=worktrees_base,
                builder_scripted_actions=builder_scripted_actions,
                max_concurrent_agents=max_concurrent_agents,
            )
            continue

        act = _decide(events, max_rework)
        if act is None:
            break
        kind, ctx = act

        if kind == "guide_decompose":
            for ev in guide_step(events, ctx, llm_client=llm_client):
                _append_guide_event(session_store, ev)

        elif kind == "dispatch_builder":
            # M2.4 多卡 test-first:按卡序分配不同实例(作者≠实现者)。ADR-016:每实例独立
            # worktree(merchant-1/merchant-2),从 main 切——依赖卡因前置卡已 merge 进 main
            # 而自带依赖产物(depends_on 门保证次序),不再共享目录。
            d = ctx
            card = d["payload"]["task_card"]
            inst = _builder_instance(events, d)
            ga = session_store.append_event(
                agent="guide", type="guide_assign", parent_event_id=d["event_id"],
                session_id=d["session_id"],
                payload={"task_card_event_id": d["event_id"], "assignee_instance": inst},
            )
            scripted = (builder_scripted_actions or {}).get(card["task_id"])
            reuse = (worktrees_base / instance_to_slug(inst)).is_dir()
            _run_builder(
                npc_in_subprocess, inst=inst, card=card, store=session_store,
                trigger_eid=ga["event_id"], repo_root=repo_root,
                worktrees_base=worktrees_base, llm_client=llm_client,
                scripted=scripted, reuse=reuse,
            )

        elif kind == "finish_build":
            # 崩溃续作:用既有的 builder guide_assign 重跑 execute_npc,补出缺失的 review_request。
            # worktree 可能已建(崩在 create 之后)或未建(崩在 create 之前)→ 据实 reuse/create。
            a = ctx
            d = next(e for e in events
                     if e["type"] == "guide_delegate"
                     and e["event_id"] == a["payload"]["task_card_event_id"])
            card = d["payload"]["task_card"]
            inst = a["payload"]["assignee_instance"]  # 续作沿用该 assign 的实例
            wt_exists = (worktrees_base / instance_to_slug(inst)).is_dir()
            scripted = (builder_scripted_actions or {}).get(card["task_id"])
            _run_builder(
                npc_in_subprocess, inst=inst, card=card, store=session_store,
                trigger_eid=a["event_id"], repo_root=repo_root,
                worktrees_base=worktrees_base, llm_client=llm_client,
                scripted=scripted, reuse=wt_exists,
            )

        elif kind == "dispatch_verifier":
            rr = ctx
            d = _delegate_for_task(events, rr["payload"]["task_id"])
            card = d["payload"]["task_card"]
            inst = ROLE_INSTANCE["verifier"]
            ga = session_store.append_event(
                agent="guide", type="guide_assign", parent_event_id=rr["event_id"],
                session_id=rr["session_id"],
                payload={"task_card_event_id": d["event_id"], "assignee_instance": inst},
            )
            # ADR-016 merge-then-verify(撤 ADR-014 直接进 builder worktree):把 builder
            # 分支 detached 签出到独立验证 worktree,verifier 在隔离签出内只读+执行,用后即销。
            builder_inst = _builder_instance(events, d)
            verify_path = worktrees_base / f"verify-{instance_to_slug(builder_inst)}"
            verify_wt = add_verify_worktree(builder_inst, verify_path, repo_root=repo_root)
            try:
                verify_task(inst, card, verify_wt, session_store, ga["event_id"])
            finally:
                remove_worktree_path(verify_wt, repo_root=repo_root)

        elif kind == "dispatch_reviewer":
            vr = ctx
            d = _delegate_for_task(events, vr["payload"]["task_id"])
            card = d["payload"]["task_card"]
            inst = ROLE_INSTANCE["reviewer"]
            ga = session_store.append_event(
                agent="guide", type="guide_assign", parent_event_id=vr["event_id"],
                session_id=vr["session_id"],
                payload={"task_card_event_id": d["event_id"], "assignee_instance": inst},
            )
            # tailor 在隔离事实 context 上代码审查(机器判定权威由 review_task 强制)
            review_task(inst, card, session_store, ga["event_id"], llm_client=llm_client)

        elif kind == "arbitrate_pass":
            rv = ctx
            tid = rv["payload"]["task_id"]
            d = _delegate_for_task(events, tid)
            builder_inst = _builder_instance(events, d)
            # ADR-016 决策 5:Guide 仲裁跑真 git merge --no-ff 到 main,填真实 commit hash。
            result, commit = merge_to_main(builder_inst, repo_root=repo_root)
            payload = {"task_id": tid, "source": branch_name(builder_inst),
                       "target": "main", "result": result}
            if result == "success":
                payload["commit"] = commit
                payload["milestone"] = True
            merge_ev = session_store.append_event(
                agent="guide", type="merge", parent_event_id=rv["event_id"],
                session_id=rv["session_id"], payload=payload,
            )
            if result == "conflict":
                # 冲突不自动消解 → 落 merge(conflict) 后转 HITL 兜底(ADR-016 决策 5)
                session_store.append_event(
                    agent="guide", type="hitl_request", parent_event_id=merge_ev["event_id"],
                    session_id=rv["session_id"],
                    payload={"task_id": tid,
                             "reason": f"git merge {branch_name(builder_inst)} → main 冲突,需人工消解",
                             "question": f"任务 {tid} 合并到 main 出现冲突,请人工解决后继续。"},
                )

        elif kind == "rework":
            # M2.2b:reject → 重派同一卡的 builder 返工(复用 feature worktree,注入 reject notes)
            rv = ctx
            d = _delegate_for_task(events, rv["payload"]["task_id"])
            card = d["payload"]["task_card"]
            inst = _builder_instance(events, d)  # 同一卡 → 同一实例(稳定)
            ga = session_store.append_event(
                agent="guide", type="guide_assign", parent_event_id=rv["event_id"],
                session_id=rv["session_id"],
                payload={"task_card_event_id": d["event_id"], "assignee_instance": inst},
            )
            scripted = (builder_scripted_actions or {}).get(card["task_id"])
            _run_builder(
                npc_in_subprocess, inst=inst, card=card, store=session_store,
                trigger_eid=ga["event_id"], repo_root=repo_root,
                worktrees_base=worktrees_base, llm_client=llm_client,
                scripted=scripted, reuse=True,
                rework_notes=rv["payload"].get("notes"),
            )

        elif kind == "escalate_reject":
            # 返工次数用尽 → 上抬人工(不无限循环)
            rv = ctx
            tid = rv["payload"]["task_id"]
            session_store.append_event(
                agent="guide", type="hitl_request", parent_event_id=rv["event_id"],
                session_id=rv["session_id"],
                payload={"task_id": tid,
                         "reason": f"返工 {max_rework} 次后审查仍 reject,上抬人工",
                         "question": f"任务 {tid} 经 {max_rework} 轮返工仍未通过审查;notes: {rv['payload'].get('notes', '')}"},
            )

    return wake(session_store, worktrees_base=worktrees_base)
