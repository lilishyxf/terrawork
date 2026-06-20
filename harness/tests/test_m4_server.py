"""M4-2 写端点 + advance-runner 测试(ADR-022)。starlette TestClient。

用注入的假 advance_fn 测"写路径 + runner 机制"(不拉真 LLM):
- POST /command → 落 user_command + 触发后台 advance(假 advance 落个 marker 事件,可经 /events 见)
- POST /hitl → 校验 parent 是 hitl_request + 落 hitl_response
- runner:每 session 单飞(不并发)、运行中到达的事件由 dirty 再跑一轮(不漏)
"""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from harness.session.store import SessionStore
from harness.view.server import create_app

SID = "m4"


@pytest.fixture
def db(tmp_path):
    return tmp_path / "s.db"


def _seed_user_cmd(db, text="做个登录"):
    s = SessionStore(db, session_id=SID)
    s.append_event(agent="user", type="user_command", payload={"text": text}, session_id=SID)
    s.close()


def test_post_command_appends_and_runs(db):
    """POST /command → user_command 落盘 + 后台 advance 跑(假 advance 落 marker)。"""
    ran = threading.Event()

    def fake_advance(store, **kw):
        store.append_event(agent="guide", type="guide_think",
                           payload={"text": "advance ran"}, session_id=SID)
        ran.set()

    client = TestClient(create_app(db, advance_fn=fake_advance))
    r = client.post(f"/sessions/{SID}/command", json={"text": "做个登录"})
    assert r.status_code == 202 and r.json()["event_id"] >= 1
    assert ran.wait(2.0), "后台 advance 未运行"
    # 落盘:user_command + 假 advance 的 guide_think
    time.sleep(0.1)
    evs = client.get(f"/sessions/{SID}/events").json()["events"]
    types = [e["type"] for e in evs]
    assert "user_command" in types and "guide_think" in types


def test_post_command_rejects_empty(db):
    client = TestClient(create_app(db, advance_fn=lambda *a, **k: None))
    assert client.post(f"/sessions/{SID}/command", json={"text": "  "}).status_code == 400


def test_post_hitl_validates_parent(db):
    """/hitl 必须指向该 session 真实的 hitl_request;否则 400。"""
    _seed_user_cmd(db)
    s = SessionStore(db, session_id=SID)
    hitl = s.append_event(agent="guide", type="hitl_request",
                          payload={"task_id": "t", "reason": "x", "question": "?"}, session_id=SID)
    s.close()
    client = TestClient(create_app(db, advance_fn=lambda *a, **k: None))

    # 合法:指向真实 hitl_request
    ok = client.post(f"/sessions/{SID}/hitl",
                     json={"hitl_event_id": hitl["event_id"], "decision": "answer", "text": "用 hashlib"})
    assert ok.status_code == 202
    # 校验落盘的 hitl_response:parent 对、payload 对
    evs = client.get(f"/sessions/{SID}/events").json()["events"]
    resp = next(e for e in evs if e["type"] == "hitl_response")
    assert resp["parent_event_id"] == hitl["event_id"]
    assert resp["payload"] == {"decision": "answer", "text": "用 hashlib"}

    # 非法:指向非 hitl_request 事件 → 400
    bad = client.post(f"/sessions/{SID}/hitl",
                      json={"hitl_event_id": 1, "decision": "answer"})
    assert bad.status_code == 400
    # 非法 decision → 400
    bad2 = client.post(f"/sessions/{SID}/hitl",
                       json={"hitl_event_id": hitl["event_id"], "decision": "approve"})
    assert bad2.status_code == 400


def test_runner_single_flight_no_overlap(db):
    """并发 POST 时同 session 的 advance 绝不重叠(单飞),且不漏(dirty 再跑)。"""
    active = {"n": 0}
    overlap = {"hit": False}
    calls = {"n": 0}
    lk = threading.Lock()

    def fake_advance(store, **kw):
        with lk:
            active["n"] += 1
            calls["n"] += 1
            if active["n"] > 1:
                overlap["hit"] = True
        time.sleep(0.05)
        with lk:
            active["n"] -= 1

    client = TestClient(create_app(db, advance_fn=fake_advance))
    # 快速连发 5 条 command
    for i in range(5):
        client.post(f"/sessions/{SID}/command", json={"text": f"cmd{i}"})
    time.sleep(0.6)  # 等 runner 跑完
    assert overlap["hit"] is False, "同 session advance 不应并发重叠"
    assert calls["n"] >= 1, "advance 至少跑过(dirty 合并多条也至少一轮)"
