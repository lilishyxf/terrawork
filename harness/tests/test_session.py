"""M1.1 Session 层验收测试。

覆盖三条验收信号 +附加不变量：
  1. "做个登录"事件链可灌入并按 parent_event_id 回放出因果树；
  2. 非法事件（自然语言谓词 / 缺 parent / 未知 type）被 schema 校验拦下；
  3. append-only：UPDATE / DELETE 被数据库触发器拒绝；
  附：event_id 自增连续；parent 不存在时因果检查拒绝。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from harness.session import (
    CausalityError,
    SchemaError,
    SessionStore,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _insert_remapped(store: SessionStore, events: list[dict]) -> dict:
    """按 CLI 同样的逻辑灌入并重映射 fixture id。返回 old->new id 映射。"""
    idmap: dict = {}
    for raw in events:
        old_parent = raw.get("parent_event_id")
        new_parent = None if old_parent is None else idmap.get(old_parent, old_parent)
        ev = store.append_event(
            agent=raw["agent"],
            type=raw["type"],
            payload=raw["payload"],
            parent_event_id=new_parent,
            session_id=raw.get("session_id"),
            ts=raw.get("ts"),
        )
        idmap[raw["event_id"]] = ev["event_id"]
    return idmap


@pytest.fixture()
def store(tmp_path) -> SessionStore:
    s = SessionStore(tmp_path / "session.db", session_id="s-login-demo")
    yield s
    s.close()


# --- 信号 1：灌入 + 因果回放 -------------------------------------------------
def test_login_chain_inserts_and_replays(store: SessionStore):
    events = _load("login_chain.json")
    _insert_remapped(store, events)

    all_events = store.query_session()
    assert len(all_events) == len(events) == 10

    forest = store.replay_forest()
    assert len(forest) == 1, "应只有一个根（user_command）"
    root = forest[0]
    assert root["type"] == "user_command"
    assert root["payload"]["text"] == "做个登录"

    # 根 → guide_think → guide_delegate 的链路
    guide_think = root["children"][0]
    assert guide_think["type"] == "guide_think"
    delegate = guide_think["children"][0]
    assert delegate["type"] == "guide_delegate"
    # guide_delegate 下挂着 npc_think / tool_intent / verify_run / review_request / merge
    child_types = {c["type"] for c in delegate["children"]}
    assert {"npc_think", "tool_intent", "verify_run", "review_request", "merge"} <= child_types

    # tool_done 必须挂在它配对的 tool_intent 之下（预写配对）
    tool_intent = next(c for c in delegate["children"] if c["type"] == "tool_intent")
    assert tool_intent["children"][0]["type"] == "tool_done"
    assert len(tool_intent["children"][0]["payload"]["hash"]) == 64


def test_event_id_autoincrement_contiguous(store: SessionStore):
    _insert_remapped(store, _load("login_chain.json"))
    ids = [e["event_id"] for e in store.query_session()]
    assert ids == list(range(1, 11))


# --- 信号 2：非法事件被拦下 --------------------------------------------------
def test_invalid_events_rejected(store: SessionStore):
    bad = _load("invalid_events.json")
    assert len(bad) == 3
    for raw in bad:
        with pytest.raises(SchemaError):
            store.append_event(
                agent=raw["agent"],
                type=raw["type"],
                payload=raw["payload"],
                parent_event_id=raw.get("parent_event_id"),
                session_id=raw.get("session_id"),
                ts=raw.get("ts"),
            )
    # 全部被拒，日志应仍为空
    assert store.query_session() == []


# --- 信号 3：append-only ----------------------------------------------------
def test_append_only_blocks_update_and_delete(store: SessionStore):
    _insert_remapped(store, _load("login_chain.json"))
    raw = sqlite3.connect(store.db_path)
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("UPDATE events SET agent='x' WHERE event_id=1")
    with pytest.raises(sqlite3.IntegrityError):
        raw.execute("DELETE FROM events WHERE event_id=1")
    raw.close()


# --- 附：因果完整性 ----------------------------------------------------------
def test_missing_parent_rejected(store: SessionStore):
    with pytest.raises(CausalityError):
        store.append_event(
            agent="merchant#1",
            type="npc_think",
            payload={"text": "孤儿事件"},
            parent_event_id=999,
        )
