"""Guide verdict layer (M1.4-3). Deterministic — no LLM.

Guide 消费 verify_run → 出 review_verdict:
- passed=True  → review_verdict(verdict='pass')
- passed=False → review_verdict(verdict='reject') + hitl_request 兜底
  (M1 不自动返工,直接上抬人工;多轮返工循环留 M2)

INV-5 机器判定权威:Guide 在此**无自由裁量**——verdict 完全由 verify_run.passed
机械推出,不调 LLM。这是 ADR-004 "信任问题转化为执行问题" 在仲裁端的反映。
"""
from harness.session.store import SessionStore


def decide_verdict(session_store: SessionStore, verify_run_event_id: int) -> list[int]:
    """读 verify_run → 机械出 review_verdict(+ fail 时的 hitl_request)。返回落下的事件 id。"""
    vr = session_store.get_event(verify_run_event_id)
    if vr is None or vr["type"] != "verify_run":
        raise ValueError(f"event {verify_run_event_id} is not a verify_run")

    task_id = vr["payload"]["task_id"]
    passed = vr["payload"]["passed"]
    session_id = vr["session_id"]

    if passed:
        v = session_store.append_event(
            type="review_verdict",
            agent="guide",
            parent_event_id=verify_run_event_id,
            session_id=session_id,
            payload={
                "task_id": task_id,
                "reviewer": "guide",
                "verdict": "pass",
                "notes": f"verify_run#{verify_run_event_id} passed",
            },
        )
        return [v["event_id"]]

    # fail 路径 (option b): review_verdict(reject) + hitl_request 兜底
    v = session_store.append_event(
        type="review_verdict",
        agent="guide",
        parent_event_id=verify_run_event_id,
        session_id=session_id,
        payload={
            "task_id": task_id,
            "reviewer": "guide",
            "verdict": "reject",
            "notes": (
                f"verify_run#{verify_run_event_id} failed; "
                f"M1 escalates to human (no auto-rework, deferred to M2)"
            ),
        },
    )
    h = session_store.append_event(
        type="hitl_request",
        agent="guide",
        parent_event_id=v["event_id"],
        session_id=session_id,
        payload={
            "task_id": task_id,
            "reason": (
                f"verification failed (verify_run#{verify_run_event_id}); "
                f"M1 has no auto-rework loop"
            ),
            "question": f"任务 {task_id} 验证未通过,请人工决定返工或修正后重验。",
        },
    )
    return [v["event_id"], h["event_id"]]
