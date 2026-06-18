"""M3-2 View 事件服务(ADR-021)。只读 FastAPI sidecar:把 SQLite 事件流喂给 View。

- GET  /sessions/{sid}/events?since=&limit=   catch-up 游标分页
- GET  /sessions/{sid}/snapshot               当前小镇状态(复用参考投影 ADR-020)
- WS   /sessions/{sid}/live?since=            Catch-up→Live 两阶段(ADR-001)

只读 SQLite,不写事件、不调 LLM、不驱动 advance(§1 红线#1;玩家交互留 M4)。
Live 用轮询 SQLite 发现新事件——对子进程写入(ADR-017)鲁棒,DB 是唯一真相。
"""
import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from harness.session.store import SessionStore
from harness.view.projection import project


def _read_events(db_path, sid: str, since: int = 0, limit=None) -> list[dict]:
    """开短命只读连接查 since 之后的事件(查完即关,避免跨线程共享 sqlite 连接)。"""
    store = SessionStore(db_path, session_id=sid)
    try:
        return store.query_session(
            session_id=sid,
            since_event_id=since or None,  # 0 → None(取全部);排他 event_id > since
            limit=limit,
        )
    finally:
        store.close()


def create_app(db_path, *, poll_interval: float = 0.3) -> FastAPI:
    db_path = Path(db_path)
    app = FastAPI(title="TerraWorks View API", version="0.1")

    @app.get("/sessions/{sid}/events")
    def get_events(sid: str, since: int = 0, limit: int = 500):
        evs = _read_events(db_path, sid, since, limit)
        return {"events": evs, "cursor": evs[-1]["event_id"] if evs else since, "count": len(evs)}

    @app.get("/sessions/{sid}/snapshot")
    def get_snapshot(sid: str):
        evs = _read_events(db_path, sid, 0, None)
        return {"snapshot": project(evs), "cursor": evs[-1]["event_id"] if evs else 0}

    @app.websocket("/sessions/{sid}/live")
    async def live(ws: WebSocket, sid: str, since: int = 0):
        await ws.accept()
        cursor = since
        try:
            # ---- Catch-up 阶段:补发积压(快进,客户端不播动画) ----
            for e in _read_events(db_path, sid, cursor, None):
                await ws.send_json({"type": "event", "phase": "catchup", "event": e})
                cursor = e["event_id"]
            await ws.send_json({"type": "caught_up", "cursor": cursor})
            # ---- Live 阶段:轮询新事件(客户端逐条播完整动画) ----
            while True:
                for e in _read_events(db_path, sid, cursor, None):
                    await ws.send_json({"type": "event", "phase": "live", "event": e})
                    cursor = e["event_id"]
                await asyncio.sleep(poll_interval)
        except WebSocketDisconnect:
            return

    return app
