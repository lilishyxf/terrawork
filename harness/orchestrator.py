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

# role -> instance 映射。verifier 单实例;builder 不在此表——按卡的 assignee_specialty 经
# _builder_instance 动态实例化 <specialty>#N(ADR-019)。
ROLE_INSTANCE = {"verifier": "blaster#1"}

# 双审查 roster(ADR-019):每张 builder 卡完工后两位 reviewer 各审一次,全 pass 才 merge、
# 任一 reject 触发返工。tailor=代码审查、appsec=安全审查(两道独立闸,看隔离事实 context)。
REVIEWERS = ("tailor#1", "appsec#1")


def _builder_delegates(events):
    return sorted(
        [e for e in events if e["type"] == "guide_delegate"
         and e["payload"]["task_card"].get("assignee_role") == "builder"],
        key=lambda e: e["event_id"],
    )


def _card_specialty(delegate):
    """卡的 builder 专长键:assignee_specialty,缺省 merchant(向后兼容,ADR-019)。"""
    return delegate["payload"]["task_card"].get("assignee_specialty") or "merchant"


def _builder_instance(events, delegate):
    """按专长稳定分配实例:<specialty>#<同专长内出现序号>(ADR-019 双轴)。
    专长取卡的 assignee_specialty,缺省 merchant。同一专长多卡 → #1/#2…(test-first
    测试卡≠实现卡,作者≠实现者,ADR-004);无专长的旧式分解全是 merchant#1/#2…(行为不变)。"""
    target = _card_specialty(delegate)
    count = 0
    for e in _builder_delegates(events):
        if _card_specialty(e) == target:
            count += 1
            if e["event_id"] == delegate["event_id"]:
                return f"{target}#{count}"
    raise ValueError(f"delegate {delegate['event_id']} 不在 builder delegates 中")


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
        futures = {
            ex.submit(
                run_npc_subprocess,
                npc_instance_id=inst, task_card=card,
                db_path=str(store.db_path), session_id=store.session_id,
                guide_assign_event_id=eid,
                repo_root=str(repo_root), worktrees_base=str(worktrees_base),
                scripted_actions=scripted, reuse_worktree=True,
            ): (inst, card, eid)
            for (inst, card, eid, scripted) in jobs
        }
        for f in as_completed(futures):
            inst, card, eid = futures[f]
            try:
                f.result()
            except Exception as exc:  # 单 builder 失败 → 记 error,不拖垮整批/advance
                _append_builder_error(store, inst, card, eid, exc)


def _append_builder_error(store, inst, card, trigger_eid, exc):
    """builder 失败 → 落 error 事件(韧性:单 NPC 失败变成可见错误,不拖垮整个 advance;
    _decide 见到该卡有 error 即不再重派,避免无限重试)。"""
    store.append_event(
        agent=inst, type="error", parent_event_id=trigger_eid,
        session_id=store.session_id,
        payload={"kind": "exception", "message": str(exc)[:500],
                 "task_id": card["task_id"], "agent_ref": inst},
    )


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
    # 1. 逐指令分解(ADR-022):分解每一条尚未处理的 user_command(支持追加指令)。
    #    "已处理" = 有事件以该 command 为 parent(guide_step 的 guide_think.parent=trigger,
    #    或分解失败的 hitl 兜底.parent=trigger)。
    _ids_with_child = {e.get("parent_event_id") for e in events}
    for cmd in events:
        if cmd["type"] == "user_command" and cmd["event_id"] not in _ids_with_child:
            return ("guide_decompose", cmd)

    assigns = [e for e in events if e["type"] == "guide_assign"]
    # builder 实例集合(按各卡专长泛化,ADR-019;不再写死 merchant)
    builder_insts = {_builder_instance(events, d) for d in _builder_delegates(events)}

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
        # 韧性:builder 已 error 的卡不再续作/重派(否则无限重试同一失败)
        if _has_later(events, a["event_id"], {"error"}, task_id=tid):
            continue
        if not _has_later(events, a["event_id"], {"review_request"}, task_id=tid):
            return ("finish_build", a)

    # 3. review_request 之后无 verify_run → 派验证(round-aware:每轮的最新 review_request)
    for rr in events:
        if rr["type"] == "review_request":
            tid = rr["payload"]["task_id"]
            if not _has_later(events, rr["event_id"], {"verify_run"}, task_id=tid):
                return ("dispatch_verifier", rr)

    # 4. 双审查(ADR-019):每个 task 的最新 verify_run 轮后,每位 reviewer 各审一次
    for vr in events:
        if vr["type"] != "verify_run":
            continue
        tid = vr["payload"]["task_id"]
        if _has_later(events, vr["event_id"], {"verify_run"}, task_id=tid):
            continue  # 被更新一轮 verify 取代,只看最新轮
        for reviewer in REVIEWERS:
            reviewed = any(
                e["type"] == "review_verdict" and e["payload"]["task_id"] == tid
                and e["payload"].get("reviewer") == reviewer and e["event_id"] > vr["event_id"]
                for e in events
            )
            if not reviewed:
                return ("dispatch_reviewer", (vr, reviewer))

    # 5. 仲裁(双审查聚合):本轮(最新 verify_run 后)所有 reviewer verdict 齐了才裁决——
    #    全 pass → merge;任一 reject → 返工(≤ max_rework 轮)或上抬 HITL。
    for vr in events:
        if vr["type"] != "verify_run":
            continue
        tid = vr["payload"]["task_id"]
        if _has_later(events, vr["event_id"], {"verify_run"}, task_id=tid):
            continue
        round_verdicts = {}  # reviewer -> review_verdict event(本轮)
        for e in events:
            if (e["type"] == "review_verdict" and e["payload"]["task_id"] == tid
                    and e["event_id"] > vr["event_id"]):
                round_verdicts[e["payload"].get("reviewer")] = e
        if not all(r in round_verdicts for r in REVIEWERS):
            continue  # 本轮未审齐,等 stage 4 派完
        last_ev = max(round_verdicts.values(), key=lambda e: e["event_id"])

        if all(round_verdicts[r]["payload"]["verdict"] == "pass" for r in REVIEWERS):
            if not _has_later(events, last_ev["event_id"], {"merge"}, task_id=tid):
                return ("arbitrate_pass", last_ev)  # 全 pass → 合并
            continue

        # 至少一位 reject:已处理?(其后已有该卡 builder 重派 或 hitl)
        d = _delegate_for_task(events, tid)
        redispatched = d is not None and any(
            a["payload"].get("task_card_event_id") == d["event_id"]
            and a["payload"].get("assignee_instance") in builder_insts
            and a["event_id"] > last_ev["event_id"] for a in assigns
        )
        if redispatched or _has_later(events, last_ev["event_id"], {"hitl_request"}, task_id=tid):
            continue
        # 已做返工轮数 = 该卡 builder guide_assign 次数 - 1(每轮可能两位 reject,按轮计而非按 verdict)
        reworks_done = sum(
            1 for a in assigns
            if d is not None and a["payload"].get("task_card_event_id") == d["event_id"]
            and a["payload"].get("assignee_instance") in builder_insts
        ) - 1
        reject_ev = next(round_verdicts[r] for r in REVIEWERS
                         if round_verdicts[r]["payload"]["verdict"] == "reject")
        if reworks_done < max_rework:
            return ("rework", reject_ev)
        return ("escalate_reject", reject_ev)

    # 6. 双向交互(ADR-022):消费 hitl_response —— answer→重派该任务 builder(注入 text 整改指引,
    #    人授权绕过 max_rework);reject→任务终态放弃(无派发);approve v0.1 不开。一条 response 只处理一次。
    for resp in events:
        if resp["type"] != "hitl_response" or resp["payload"].get("decision") != "answer":
            continue
        hreq = next((e for e in events if e["event_id"] == resp.get("parent_event_id")
                     and e["type"] == "hitl_request"), None)
        if hreq is None:
            continue
        tid = hreq["payload"].get("task_id")
        if tid is None:
            continue  # 无 task 的 hitl(如分解失败)v0.1 不处理 answer→rework
        d = _delegate_for_task(events, tid)
        if d is None:
            continue
        inst = _builder_instance(events, d)
        already = any(a["payload"].get("task_card_event_id") == d["event_id"]
                      and a["payload"].get("assignee_instance") == inst
                      and a["event_id"] > resp["event_id"] for a in assigns)
        if not already:
            return ("hitl_rework", resp)

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
            try:
                _run_builder(
                    npc_in_subprocess, inst=inst, card=card, store=session_store,
                    trigger_eid=ga["event_id"], repo_root=repo_root,
                    worktrees_base=worktrees_base, llm_client=llm_client,
                    scripted=scripted, reuse=reuse,
                )
            except Exception as exc:  # builder 失败 → 记 error,不拖垮 advance
                _append_builder_error(session_store, inst, card, ga["event_id"], exc)

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
            try:
                _run_builder(
                    npc_in_subprocess, inst=inst, card=card, store=session_store,
                    trigger_eid=a["event_id"], repo_root=repo_root,
                    worktrees_base=worktrees_base, llm_client=llm_client,
                    scripted=scripted, reuse=wt_exists,
                )
            except Exception as exc:  # builder 失败 → 记 error,不拖垮 advance
                _append_builder_error(session_store, inst, card, a["event_id"], exc)

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
            vr, reviewer = ctx  # 双审查:reviewer ∈ REVIEWERS(tailor#1 / appsec#1)
            d = _delegate_for_task(events, vr["payload"]["task_id"])
            card = d["payload"]["task_card"]
            ga = session_store.append_event(
                agent="guide", type="guide_assign", parent_event_id=vr["event_id"],
                session_id=vr["session_id"],
                payload={"task_card_event_id": d["event_id"], "assignee_instance": reviewer},
            )
            # reviewer 在隔离事实 context 上审查(机器判定权威由 review_task 强制)
            review_task(reviewer, card, session_store, ga["event_id"], llm_client=llm_client)

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

        elif kind == "hitl_rework":
            # ADR-022:人在 HITL 给了整改指引(answer)→ 重派该任务 builder(注入 text,绕过 max_rework)
            resp = ctx
            hreq = next(e for e in events if e["event_id"] == resp["parent_event_id"])
            d = _delegate_for_task(events, hreq["payload"]["task_id"])
            card = d["payload"]["task_card"]
            inst = _builder_instance(events, d)
            ga = session_store.append_event(
                agent="guide", type="guide_assign", parent_event_id=resp["event_id"],
                session_id=resp["session_id"],
                payload={"task_card_event_id": d["event_id"], "assignee_instance": inst},
            )
            scripted = (builder_scripted_actions or {}).get(card["task_id"])
            reuse = (worktrees_base / instance_to_slug(inst)).is_dir()
            _run_builder(
                npc_in_subprocess, inst=inst, card=card, store=session_store,
                trigger_eid=ga["event_id"], repo_root=repo_root,
                worktrees_base=worktrees_base, llm_client=llm_client,
                scripted=scripted, reuse=reuse,
                rework_notes=resp["payload"].get("text"),
            )

    return wake(session_store, worktrees_base=worktrees_base)
