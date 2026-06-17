"""M1.4-4 wake(sessionId) crash recovery (ARCHITECTURE §8).

- m14c self-consistency: replay crashed session -> wake -> task board + unpaired match expected
- hash-compare e2e: unpaired write intent with file present -> reconcile; absent -> redo
确定性、离线(不调 LLM)。
"""
import json
from pathlib import Path

from harness.session.store import SessionStore
from harness.wake import wake

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _replay(store: SessionStore, events: list[dict]) -> None:
    """按真实 append_event API 回放 fixture 事件(id 连续从 1 → 与 fixture id 同构)。"""
    for ev in events:
        store.append_event(
            agent=ev["agent"], type=ev["type"], payload=ev["payload"],
            parent_event_id=ev["parent_event_id"], session_id=ev["session_id"], ts=ev["ts"],
        )


def test_m14c_wake_reconstructs_task_board(tmp_path):
    """m14c self-consistency: 崩溃 session 重放后 wake 重建任务板 + 检出 unpaired。"""
    fx = load_fixture("m14c_wake_recovery.json")
    store = SessionStore(tmp_path / "session.db")
    _replay(store, fx["session"])

    # worktrees_base 为空目录 → unpaired 文件不存在 → 不影响任务板/检出(只影响 reconcile|redo)
    report = wake(store, worktrees_base=tmp_path / "worktrees")

    # 任务板状态机械重建
    board = {tid: info["status"] for tid, info in report["task_board"].items()}
    assert board == fx["expected_task_board"], f"task_board {board} != {fx['expected_task_board']}"

    # unpaired intent 检出(event_id 与 fixture 同构)
    unpaired_ids = sorted(u["event_id"] for u in report["unpaired_intents"])
    assert unpaired_ids == fx["expected_unpaired_intent_event_ids"], \
        f"unpaired {unpaired_ids} != {fx['expected_unpaired_intent_event_ids']}"


def _seed_unpaired_intent(store, agent, path, worktrees_base, write_file: bool):
    """造一个 unpaired write tool_intent(无 tool_done);可选在 worktree 内真写文件。"""
    de = store.append_event(agent="guide", type="guide_delegate", session_id="wake-e2e", payload={"task_card": {
        "task_id": "t-x", "assignee_role": "builder", "objective": "x", "output_format": "x",
        "allowed_tools": ["write"], "boundaries": ["x"],
        "verification": [{"type": "machine_verifiable", "command": "true", "expected": {"exit_code": 0}}]}})
    ga = store.append_event(agent="guide", type="guide_assign", parent_event_id=de["event_id"],
                            session_id="wake-e2e",
                            payload={"task_card_event_id": de["event_id"], "assignee_instance": agent})
    it = store.append_event(agent=agent, type="tool_intent", parent_event_id=ga["event_id"],
                            session_id="wake-e2e",
                            payload={"tool": "write", "params": {"path": path}, "task_id": "t-x"})
    if write_file:
        from harness.sandbox.worktree import instance_to_slug
        wt = worktrees_base / instance_to_slug(agent)
        wt.mkdir(parents=True, exist_ok=True)
        (wt / path).write_text("recovered content\n", encoding="utf-8")
    return it["event_id"]


def test_wake_hash_compare_classifies_reconcile_vs_redo(tmp_path):
    """runtime: unpaired write intent 文件存在 → reconcile(+hash);文件缺失 → redo。"""
    store = SessionStore(tmp_path / "session.db")
    wtb = tmp_path / "worktrees"

    present_eid = _seed_unpaired_intent(store, "merchant#1", "present.py", wtb, write_file=True)
    absent_eid = _seed_unpaired_intent(store, "merchant#2", "absent.py", wtb, write_file=False)

    report = wake(store, worktrees_base=wtb)
    by_eid = {u["event_id"]: u for u in report["unpaired_intents"]}

    assert present_eid in by_eid and absent_eid in by_eid

    present = by_eid[present_eid]
    assert present["file_exists"] is True
    assert present["recommendation"] == "reconcile"
    assert present["actual_hash"] and len(present["actual_hash"]) == 64

    absent = by_eid[absent_eid]
    assert absent["file_exists"] is False
    assert absent["recommendation"] == "redo"
    assert absent["actual_hash"] is None
