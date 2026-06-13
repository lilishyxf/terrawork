"""Append-only Session 日志（SQLite WAL）。M1.1 —— 哑存储 + 哑校验。

红线（ARCHITECTURE 第 4 节 / 铁律④）：
  * 事件表仅 INSERT；UPDATE / DELETE 由数据库触发器 + 接口层双重禁止。
  * 写入即校验：append_event 组装完整事件 → events.schema.json 校验 → 通过才落盘。
  * event_id 自增；parent_event_id 维护因果链，非空时校验其指向的事件确实存在。

本层不含 Guide、不调 LLM、不碰 sandbox。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .schema import SchemaError, validate_event  # noqa: F401  (SchemaError re-export)

__all__ = ["SessionStore", "SchemaError", "CausalityError", "AppendOnlyError"]


class CausalityError(ValueError):
    """parent_event_id 指向不存在的事件（因果链断裂）。"""


class AppendOnlyError(RuntimeError):
    """试图 UPDATE / DELETE 仅追加日志。"""


_SENTINEL = object()

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    ts              TEXT    NOT NULL,
    agent           TEXT    NOT NULL,
    type            TEXT    NOT NULL,
    payload         TEXT    NOT NULL,            -- JSON 文本（人眼可读，非 blob）
    parent_event_id INTEGER REFERENCES events(event_id)
);

-- 仅追加：任何 UPDATE / DELETE 直接 ABORT（铁律④）。
CREATE TRIGGER IF NOT EXISTS events_no_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'append-only: Session 日志禁止 UPDATE');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'append-only: Session 日志禁止 DELETE');
END;

CREATE INDEX IF NOT EXISTS idx_events_parent  ON events(parent_event_id);
CREATE INDEX IF NOT EXISTS idx_events_type    ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class SessionStore:
    """单文件 SQLite（WAL）上的仅追加事件日志。"""

    def __init__(self, db_path: str | Path = "data/session.db", *, session_id: str = "default"):
        self.db_path = Path(db_path)
        self.session_id = session_id
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    # ------------------------------------------------------------------ 写入
    def append_event(
        self,
        *,
        agent: str,
        type: str,
        payload: dict,
        parent_event_id: Optional[int] = None,
        session_id: Optional[str] = None,
        ts: Optional[str] = None,
    ) -> dict:
        """组装完整事件 → schema 校验 → 落盘。返回最终落盘事件（含 event_id）。

        校验或因果检查失败则整笔回滚、不留任何痕迹（仅追加，且失败不落盘）。
        """
        session_id = session_id or self.session_id
        ts = ts or _utc_now_iso()
        conn = self._conn
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")  # 锁定，使 MAX(event_id)+1 与 INSERT 原子
        try:
            next_id = cur.execute(
                "SELECT COALESCE(MAX(event_id), 0) + 1 FROM events"
            ).fetchone()[0]

            if parent_event_id is not None:
                exists = cur.execute(
                    "SELECT 1 FROM events WHERE event_id = ?", (parent_event_id,)
                ).fetchone()
                if not exists:
                    raise CausalityError(
                        f"parent_event_id={parent_event_id} 不存在；"
                        "父事件必须先于子事件落盘"
                    )

            event = {
                "event_id": next_id,
                "session_id": session_id,
                "ts": ts,
                "agent": agent,
                "type": type,
                "parent_event_id": parent_event_id,
                "payload": payload,
            }
            validate_event(event)  # 不通过 → SchemaError → 回滚 → 不落盘

            cur.execute(
                "INSERT INTO events "
                "(event_id, session_id, ts, agent, type, payload, parent_event_id) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    next_id,
                    session_id,
                    ts,
                    agent,
                    type,
                    json.dumps(payload, ensure_ascii=False),
                    parent_event_id,
                ),
            )
            conn.commit()
            return event
        except Exception:
            conn.rollback()
            raise

    # ------------------------------------------------------------------ 读取
    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> dict:
        return {
            "event_id": row["event_id"],
            "session_id": row["session_id"],
            "ts": row["ts"],
            "agent": row["agent"],
            "type": row["type"],
            "parent_event_id": row["parent_event_id"],
            "payload": json.loads(row["payload"]),
        }

    def get_event(self, event_id: int) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return self._row_to_event(row) if row else None

    def query_session(
        self,
        *,
        session_id: Optional[str] = None,
        type: Optional[str] = None,
        types: Optional[list[str]] = None,
        agent: Optional[str] = None,
        parent_event_id: Any = _SENTINEL,
        task_id: Optional[str] = None,
        since_event_id: Optional[int] = None,
        until_event_id: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """按过滤条件查询事件（恒按 event_id 升序）。

        提供 L3"按需自查历史"的能力（ARCHITECTURE 第 8 节），亦是 catch-up
        游标（since_event_id / until_event_id 对应 last_event_id）的基础。
        ``parent_event_id`` 显式传 ``None`` 表示只取根事件；不传则不过滤该字段。
        """
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?"); params.append(session_id)
        if type is not None:
            clauses.append("type = ?"); params.append(type)
        if types:
            clauses.append(f"type IN ({','.join('?' * len(types))})"); params.extend(types)
        if agent is not None:
            clauses.append("agent = ?"); params.append(agent)
        if parent_event_id is not _SENTINEL:
            if parent_event_id is None:
                clauses.append("parent_event_id IS NULL")
            else:
                clauses.append("parent_event_id = ?"); params.append(parent_event_id)
        if task_id is not None:
            clauses.append("json_extract(payload, '$.task_id') = ?"); params.append(task_id)
        if since_event_id is not None:
            clauses.append("event_id > ?"); params.append(since_event_id)
        if until_event_id is not None:
            clauses.append("event_id <= ?"); params.append(until_event_id)

        sql = "SELECT * FROM events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY event_id ASC"
        if limit is not None:
            sql += " LIMIT ?"; params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    # --------------------------------------------------------------- 因果回放
    def replay_forest(self, *, session_id: Optional[str] = None) -> list[dict]:
        """按 parent_event_id 重建因果树森林（根 = parent 为 NULL 的事件）。

        返回 ``[{event..., "children": [...]}]``，children 按 event_id 升序。
        """
        events = self.query_session(session_id=session_id)
        nodes = {e["event_id"]: {**e, "children": []} for e in events}
        roots: list[dict] = []
        for e in events:
            node = nodes[e["event_id"]]
            pid = e["parent_event_id"]
            if pid is None or pid not in nodes:
                roots.append(node)
            else:
                nodes[pid]["children"].append(node)
        return roots

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SessionStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
