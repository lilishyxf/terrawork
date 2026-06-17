"""Reviewer (tailor) execution layer (M2.1, decision 甲). 代码审查出 review_verdict。

tailor 在**物理隔离的事实 context**上做代码审查(铁律③ / §5:看代码/工具/测试事实,
不看制作者 npc_think)。与爆破专家(机器测试 verify_run)是两道独立闸。

机器判定权威(INV-5)由代码强制,凌驾 LLM:
- 任务最新 verify_run.passed == False → 直接 reject(跳过 LLM,机器挂了不看代码)
- 无"通过的 verify_run" → 即使 LLM 说 pass 也降级为 reject

reviewer 不进 worktree、不用工具——它从隔离 context 判断(M2.1)。
"""
import json

from harness.context.assemble import assemble_review_context, load_role_frontmatter
from harness.llm import get_llm_client


def _role_of(instance_id: str) -> str:
    return instance_id.split("#", 1)[0]


def _parse_review(raw: str) -> tuple[str, str]:
    """解析 tailor LLM 输出 {verdict, notes};不合法保守判 reject(fail-safe,不误 pass)。"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "reject", "审查输出非合法 JSON,保守判 reject"
    verdict = data.get("verdict")
    if verdict not in ("pass", "reject"):
        return "reject", f"审查输出 verdict 非法({verdict!r}),保守判 reject"
    return verdict, str(data.get("notes", ""))


def review_task(
    reviewer_instance_id: str,   # e.g. "tailor#1"
    task_card: dict,
    session_store,
    guide_assign_event_id: int,  # 派 reviewer 的 guide_assign(parent 锚点)
    *,
    llm_client=None,
) -> list[int]:
    """tailor 审查 → 出 review_verdict。返回落下的事件 id。"""
    task_id = task_card["task_id"]
    trigger = session_store.get_event(guide_assign_event_id)
    if trigger is None:
        raise ValueError(f"guide_assign event {guide_assign_event_id} not found")
    session_id = trigger["session_id"]
    events = session_store.query_session(session_id=session_id)

    vruns = [e for e in events
             if e["type"] == "verify_run" and e["payload"]["task_id"] == task_id]
    machine_failed = bool(vruns) and not vruns[-1]["payload"]["passed"]
    has_passing_vr = any(e["payload"]["passed"] for e in vruns)

    if machine_failed:
        # 机器判定权威:测试挂了直接 reject,不调 LLM(不看代码)
        verdict = "reject"
        notes = f"机器验证失败(verify_run#{vruns[-1]['event_id']}),代码审查跳过(机器判定权威)"
    else:
        if llm_client is None:
            llm_client = get_llm_client()
        role = _role_of(reviewer_instance_id)
        model = load_role_frontmatter(role).get("model")
        messages = assemble_review_context(task_card, events, role_name=role)
        verdict, notes = _parse_review(llm_client.complete(model=model, messages=messages))
        # 机器判定权威(后置硬约束):无通过的 verify_run 不得 pass,即便 LLM 说 pass
        if verdict == "pass" and not has_passing_vr:
            verdict = "reject"
            notes = "无通过的 verify_run,机器判定权威下不得 pass;" + notes

    ev = session_store.append_event(
        type="review_verdict",
        agent=reviewer_instance_id,
        parent_event_id=guide_assign_event_id,
        session_id=session_id,
        payload={
            "task_id": task_id,
            "reviewer": reviewer_instance_id,
            "verdict": verdict,
            "notes": notes,
        },
    )
    return [ev["event_id"]]
