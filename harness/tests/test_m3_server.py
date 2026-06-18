"""M3-2 View 事件服务测试(ADR-021)。starlette TestClient(HTTP + WS)。"""
import pytest
from fastapi.testclient import TestClient

from harness.session.store import SessionStore
from harness.view.server import create_app

SID = "vsess"


@pytest.fixture
def db(tmp_path):
    return tmp_path / "session.db"


def _seed(db, n):
    store = SessionStore(db, session_id=SID)
    ids = []
    for i in range(n):
        e = store.append_event(agent="guide", type="guide_think",
                               payload={"text": f"t{i}"}, session_id=SID)
        ids.append(e["event_id"])
    store.close()
    return ids


def test_catchup_cursor_and_count(db):
    ids = _seed(db, 5)
    client = TestClient(create_app(db))
    r = client.get(f"/sessions/{SID}/events", params={"since": 0})
    body = r.json()
    assert body["count"] == 5 and body["cursor"] == ids[-1]
    assert [e["event_id"] for e in body["events"]] == ids
    # since 排他:since=ids[2] 只返其后
    r2 = client.get(f"/sessions/{SID}/events", params={"since": ids[2]}).json()
    assert [e["event_id"] for e in r2["events"]] == ids[3:]


def test_catchup_pagination(db):
    ids = _seed(db, 5)
    client = TestClient(create_app(db))
    got, cursor = [], 0
    while True:
        body = client.get(f"/sessions/{SID}/events", params={"since": cursor, "limit": 2}).json()
        got += [e["event_id"] for e in body["events"]]
        cursor = body["cursor"]
        if body["count"] < 2:
            break
    assert got == ids


def test_snapshot_matches_projection(db):
    _seed(db, 3)
    client = TestClient(create_app(db))
    snap = client.get(f"/sessions/{SID}/snapshot").json()
    # 固定班子在场;guide 因 guide_think → thinking
    assert "guide" in snap["snapshot"]["npcs"]
    assert snap["snapshot"]["npcs"]["guide"]["state"] == "thinking"
    assert snap["cursor"] == 3


def test_ws_catchup_then_live(db):
    ids = _seed(db, 2)
    app = create_app(db, poll_interval=0.02)
    client = TestClient(app)
    with client.websocket_connect(f"/sessions/{SID}/live?since=0") as ws:
        # 积压两条(phase catchup)
        f1 = ws.receive_json(); f2 = ws.receive_json()
        assert f1["phase"] == "catchup" and f1["event"]["event_id"] == ids[0]
        assert f2["phase"] == "catchup" and f2["event"]["event_id"] == ids[1]
        done = ws.receive_json()
        assert done["type"] == "caught_up" and done["cursor"] == ids[1]
        # 连接后新 append → 应以 phase live 推达
        store = SessionStore(db, session_id=SID)
        new = store.append_event(agent="merchant#1", type="tool_intent",
                                 payload={"tool": "write"}, session_id=SID)
        store.close()
        live = ws.receive_json()
        assert live["type"] == "event" and live["phase"] == "live"
        assert live["event"]["event_id"] == new["event_id"]


def test_ws_since_respects_cursor(db):
    ids = _seed(db, 4)
    app = create_app(db, poll_interval=0.02)
    client = TestClient(app)
    with client.websocket_connect(f"/sessions/{SID}/live?since={ids[1]}") as ws:
        # since=ids[1] → 只补发其后两条
        f = ws.receive_json()
        assert f["event"]["event_id"] == ids[2] and f["phase"] == "catchup"
        f = ws.receive_json()
        assert f["event"]["event_id"] == ids[3]
        done = ws.receive_json()
        assert done["type"] == "caught_up" and done["cursor"] == ids[3]
