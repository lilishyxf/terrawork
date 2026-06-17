"""Harness orchestration loop (M1.4-5). 驱动一个 session: user_command -> verdict。

事件驱动:每轮扫 Session 找"可行动状态",调对应组件,循环到静止(quiescent)。
M1 单卡流(M1.4-5 决策 B):guide 分解 -> 派 builder 执行 -> 派 verifier 验证 -> Guide verdict。
多卡 test-first 编排留 M2。

组件归属:
- LLM 组件:guide_step(分解) / execute_npc(builder 迭代)
- 确定性组件:verify_task / decide_verdict / wake

guide_step 是纯函数(返回事件、不写 store),其余组件直接 append 进 store——编排器负责把
guide_step 的返回事件落盘(decision: 编排器桥接两种风格)。
"""
from pathlib import Path

from harness.guide.step import guide_step
from harness.guide.verdict import decide_verdict
from harness.sandbox.executor import execute_npc
from harness.sandbox.verify_executor import verify_task
from harness.sandbox.worktree import instance_to_slug
from harness.wake import wake

# 决策②:M1 单实例 role -> instance 映射
ROLE_INSTANCE = {"builder": "merchant#1", "verifier": "blaster#1"}


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

    # 4. verify_run 未裁决
    judged = {e["payload"]["task_id"] for e in events if e["type"] == "review_verdict"}
    for vr in events:
        if vr["type"] == "verify_run" and vr["payload"]["task_id"] not in judged:
            return ("verdict", vr)

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

        elif kind == "verdict":
            decide_verdict(session_store, ctx["event_id"])

    return wake(session_store, worktrees_base=worktrees_base)
