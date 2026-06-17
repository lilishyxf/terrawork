"""Harness crash recovery (M1.4-4). wake(sessionId) 最小版 — ARCHITECTURE §8。

纯读、确定性、不调 LLM:
- 扫 Session 重建任务板视图(queued / in_progress / verified_pass / rejected)
- 检测 unpaired tool_intent(intent 无配对 tool_done)
- 对 unpaired intent 比对实际文件 hash,给出 reconcile|redo 建议

**不写任何事件、不重新派发**(§8 步骤 4 的 re-dispatch 需 LLM,留 M1.4-5/M2)。
"恢复状态不恢复思维"(§8):wake 产出可重建的状态视图,不恢复 LLM 上下文。
"""
import hashlib
from pathlib import Path

from harness.sandbox.worktree import instance_to_slug


def wake(session_store, worktrees_base: Path = Path("data/worktrees"), session_id=None) -> dict:
    """从 Session 日志重建状态视图。返回 WakeReport(纯读,无副作用)。

    Returns:
        {
          "session_id": str | None,
          "task_board": {task_id: {"status": ..., "delegate_event_id": int}},
          "unpaired_intents": [
            {"event_id", "agent", "tool", "path", "file_exists", "actual_hash", "recommendation"}
          ],
        }
    """
    events = (session_store.query_session(session_id=session_id)
              if session_id is not None else session_store.query_session())

    delegates = [e for e in events if e["type"] == "guide_delegate"]
    assigns = [e for e in events if e["type"] == "guide_assign"]
    verdicts = [e for e in events if e["type"] == "review_verdict"]
    hitls = [e for e in events if e["type"] == "hitl_request"]
    intents = [e for e in events if e["type"] == "tool_intent"]
    dones = [e for e in events if e["type"] == "tool_done"]

    # --- 任务板:按 task_id(取自 delegate.task_card)机械推导状态 ---
    task_board = {}
    for de in delegates:
        tid = de["payload"]["task_card"]["task_id"]
        assigned = any(
            a["payload"].get("task_card_event_id") == de["event_id"] for a in assigns
        )
        v_pass = any(
            v["payload"]["task_id"] == tid and v["payload"]["verdict"] == "pass"
            for v in verdicts
        )
        v_reject = any(
            v["payload"]["task_id"] == tid and v["payload"]["verdict"] == "reject"
            for v in verdicts
        )
        escalated = any(h["payload"].get("task_id") == tid for h in hitls)

        if v_pass:
            status = "verified_pass"
        elif v_reject or escalated:
            status = "rejected"
        elif assigned:
            status = "in_progress"
        else:
            status = "queued"
        task_board[tid] = {"status": status, "delegate_event_id": de["event_id"]}

    # --- unpaired intent:intent.event_id 不是任何 tool_done 的 parent ---
    done_parents = {d["parent_event_id"] for d in dones}
    unpaired = []
    for it in intents:
        if it["event_id"] in done_parents:
            continue
        agent = it["agent"]
        params = it["payload"].get("params", {})
        path = params.get("path") or params.get("file")
        rec = {
            "event_id": it["event_id"],
            "agent": agent,
            "tool": it["payload"].get("tool"),
            "path": path,
            "file_exists": False,
            "actual_hash": None,
            "recommendation": "redo",
        }
        # 有文件路径才能比对 hash;文件存在 → 写已完成 → 可补记(reconcile),否则重做(redo)
        if path:
            try:
                fp = worktrees_base / instance_to_slug(agent) / path
            except ValueError:
                fp = None  # agent 非合法实例 ID(如 guide),无 worktree
            if fp is not None and fp.is_file():
                rec["file_exists"] = True
                rec["actual_hash"] = hashlib.sha256(fp.read_bytes()).hexdigest()
                rec["recommendation"] = "reconcile"
        unpaired.append(rec)

    return {
        "session_id": events[0]["session_id"] if events else None,
        "task_board": task_board,
        "unpaired_intents": unpaired,
    }
