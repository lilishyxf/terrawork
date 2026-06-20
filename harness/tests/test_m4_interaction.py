"""M4-1 双向交互写半边契约自洽(data-scope,ADR-022)。

锁 hitl_response / 追加 user_command 的事件形态;回应→返工/放弃、追加→新分解(runtime)
由 e2e(M4-2/3)验证。
"""
import json
from pathlib import Path

import pytest

from harness.session.schema import validate_event, SchemaError

FIXTURE = Path(__file__).parent / "fixtures" / "m4_interaction.json"


def _fx():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_event_shapes_pass_schema():
    fx = _fx()
    data_invs = [i for i in fx["invariants"] if i["scope"] == "data"]
    assert len(data_invs) == 3
    for e in fx["events"]:
        validate_event(e)  # 全部过 schema(含 hitl_response 的 parent 整数约束)


def test_hitl_response_links_to_request():
    """INV-1/INV-3:hitl_response.parent 指向同 session 的 hitl_request;decision 合法。"""
    fx = _fx()
    by_id = {e["event_id"]: e for e in fx["events"]}
    resps = [e for e in fx["events"] if e["type"] == "hitl_response"]
    assert resps, "fixture 应含 hitl_response"
    for r in resps:
        assert r["payload"]["decision"] in ("approve", "reject", "answer")
        parent = by_id.get(r["parent_event_id"])
        assert parent is not None and parent["type"] == "hitl_request", \
            "hitl_response.parent 必须指向 hitl_request"
        assert parent["session_id"] == r["session_id"]


def test_followup_user_command():
    """INV-2:除首条外,追加的 user_command 也合法(支持下多条指令)。"""
    fx = _fx()
    cmds = [e for e in fx["events"] if e["type"] == "user_command"]
    assert len(cmds) >= 2, "应有追加的第二条 user_command"
    for c in cmds:
        validate_event(c)
        assert c["payload"]["text"]


def test_answer_requires_text_semantics():
    """answer 决策应带 text(整改指引);schema 不强制但 v0.1 语义需要——锁在测试里。"""
    fx = _fx()
    for r in (e for e in fx["events"] if e["type"] == "hitl_response"):
        if r["payload"]["decision"] == "answer":
            assert r["payload"].get("text"), "answer 必须带整改指引 text(注入 rework_notes)"


def test_bad_hitl_response_rejected():
    """防呆:无 parent 的 hitl_response 被 schema 拒(ADR-007 双约束)。"""
    bad = {"event_id": 1, "session_id": "m4", "ts": "2026-06-21T00:00:00Z",
           "agent": "user", "type": "hitl_response", "parent_event_id": None,
           "payload": {"decision": "answer", "text": "x"}}
    with pytest.raises(SchemaError):
        validate_event(bad)
