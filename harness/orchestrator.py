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
from pathlib import Path

from harness.guide.step import guide_step
from harness.sandbox.executor import execute_npc
from harness.sandbox.verify_executor import verify_task
from harness.sandbox.review_executor import review_task
from harness.sandbox.worktree import instance_to_slug, branch_name
from harness.wake import wake

# M1 单实例 role -> instance 映射
ROLE_INSTANCE = {"builder": "merchant#1", "verifier": "blaster#1", "reviewer": "tailor#1"}


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


def _decide(events):
    """返回下一个可行动 (kind, ctx) 或 None(静止)。每个动作执行后其条件不再触发,保证收敛。"""
    has_cmd = any(e["type"] == "user_command" for e in events)
    # 1. user_command 未分解(且 guide 未走 hitl 兜底)
    if has_cmd and not any(e["type"] in ("guide_delegate", "hitl_request") for e in events):
        return ("guide_decompose", next(e for e in events if e["type"] == "user_command"))

    assigns = [e for e in events if e["type"] == "guide_assign"]

    # 2. builder 卡未派发执行(无引用它的 builder guide_assign)
    builder_inst = ROLE_INSTANCE["builder"]
    for d in events:
        if d["type"] != "guide_delegate":
            continue
        if d["payload"]["task_card"].get("assignee_role") != "builder":
            continue
        assigned = any(
            a["payload"].get("task_card_event_id") == d["event_id"]
            and a["payload"].get("assignee_instance") == builder_inst
            for a in assigns
        )
        if not assigned:
            return ("dispatch_builder", d)

    # 3. review_request 未验证(其 task 无 verify_run)
    verified = {e["payload"]["task_id"] for e in events if e["type"] == "verify_run"}
    for rr in events:
        if rr["type"] == "review_request" and rr["payload"]["task_id"] not in verified:
            return ("dispatch_verifier", rr)

    # 4. verify_run 未审查(其 task 无 review_verdict)→ 派 tailor 代码审查(decision 甲)
    reviewed = {e["payload"]["task_id"] for e in events if e["type"] == "review_verdict"}
    for vr in events:
        if vr["type"] == "verify_run" and vr["payload"]["task_id"] not in reviewed:
            return ("dispatch_reviewer", vr)

    # 5. review_verdict 未仲裁(其 task 无 merge 也无 hitl_request)→ Guide 仲裁
    arbitrated = {
        e["payload"]["task_id"] for e in events
        if e["type"] in ("merge", "hitl_request") and "task_id" in e["payload"]
    }
    for rv in events:
        if rv["type"] == "review_verdict" and rv["payload"]["task_id"] not in arbitrated:
            return ("arbitrate", rv)

    return None


def advance(
    session_store,
    *,
    llm_client=None,
    repo_root: Path = Path("."),
    worktrees_base: Path = Path("data/worktrees"),
    builder_scripted_actions=None,
    max_steps: int = 50,
) -> dict:
    """驱动 session 到静止,返回 wake() 最终状态视图。

    builder_scripted_actions: {task_id: [actions]} 测试注入(离线确定性);None=live LLM builder。
    """
    for _ in range(max_steps):
        events = session_store.query_session()
        act = _decide(events)
        if act is None:
            break
        kind, ctx = act

        if kind == "guide_decompose":
            for ev in guide_step(events, ctx, llm_client=llm_client):
                _append_guide_event(session_store, ev)

        elif kind == "dispatch_builder":
            d = ctx
            # 决策 B 边界:M1 单卡流。多 builder 卡会共享 merchant#1 worktree(create_worktree
            # 第二次 FileExistsError)。在此显式拦成清晰的 M1-scope 报错,多卡 test-first 留 M2。
            builder_cards = [
                e for e in events
                if e["type"] == "guide_delegate"
                and e["payload"]["task_card"].get("assignee_role") == "builder"
            ]
            if len(builder_cards) > 1:
                raise NotImplementedError(
                    f"M1 orchestrator supports a single builder card (decision B); "
                    f"guide produced {len(builder_cards)} builder cards — "
                    f"multi-card test-first orchestration deferred to M2"
                )
            card = d["payload"]["task_card"]
            inst = ROLE_INSTANCE["builder"]
            ga = session_store.append_event(
                agent="guide", type="guide_assign", parent_event_id=d["event_id"],
                session_id=d["session_id"],
                payload={"task_card_event_id": d["event_id"], "assignee_instance": inst},
            )
            scripted = (builder_scripted_actions or {}).get(card["task_id"])
            execute_npc(
                inst, card, session_store, ga["event_id"],
                repo_root=repo_root, worktrees_base=worktrees_base,
                llm_client=llm_client, scripted_actions=scripted,
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
            # ADR-014: verifier 在 builder 的 worktree 内只读+执行
            builder_wt = worktrees_base / instance_to_slug(ROLE_INSTANCE["builder"])
            verify_task(inst, card, builder_wt, session_store, ga["event_id"])

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

        elif kind == "arbitrate":
            rv = ctx
            tid = rv["payload"]["task_id"]
            if rv["payload"]["verdict"] == "pass":
                # Guide 仲裁合并(M1 单 worktree:记录性 merge,真 git merge 留 M2.6)
                session_store.append_event(
                    agent="guide", type="merge", parent_event_id=rv["event_id"],
                    session_id=rv["session_id"],
                    payload={"task_id": tid, "source": branch_name(ROLE_INSTANCE["builder"]),
                             "target": "main", "result": "success", "milestone": True},
                )
            else:
                # M2.2a:reject 暂上抬人工;返工循环(reject -> re-dispatch builder)留 M2.2b
                session_store.append_event(
                    agent="guide", type="hitl_request", parent_event_id=rv["event_id"],
                    session_id=rv["session_id"],
                    payload={"task_id": tid,
                             "reason": "代码审查 reject(M2.2a 暂上抬人工,返工循环留 M2.2b)",
                             "question": f"任务 {tid} 审查未通过;notes: {rv['payload'].get('notes', '')}"},
                )

    return wake(session_store, worktrees_base=worktrees_base)
