"""View 事件服务(ADR-021 读 + ADR-022 写)。FastAPI sidecar。

读(只读):
- GET  /sessions/{sid}/events?since=&limit=   catch-up 游标分页
- GET  /sessions/{sid}/snapshot               当前小镇状态(参考投影 ADR-020)
- WS   /sessions/{sid}/live?since=            Catch-up→Live 两阶段(ADR-001)
写(ADR-022,M4):玩家操作 → user_* 事件 → 后台 advance → 结果经 live 流回(§1 红线#1)
- POST /sessions/{sid}/command  {text}                       下指令(新/追加任务)
- POST /sessions/{sid}/hitl     {hitl_event_id, decision, text?}  回应 HITL 卡口

advance-runner:每 session 单飞(threading + dirty 标志)——运行中到达的事件由 dirty
触发再跑一轮(不漏),同 session 绝不并发(不抢 worktree)。Live 用轮询 SQLite(对子进程写鲁棒)。
"""
import asyncio
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from harness.session.store import SessionStore
from harness.view.projection import project
from harness.orchestrator import advance as _real_advance


def _read_events(db_path, sid: str, since: int = 0, limit=None) -> list[dict]:
    """开短命只读连接查 since 之后的事件(查完即关,避免跨线程共享 sqlite 连接)。"""
    store = SessionStore(db_path, session_id=sid)
    try:
        return store.query_session(session_id=sid, since_event_id=since or None, limit=limit)
    finally:
        store.close()


class _CommandBody(BaseModel):
    text: str


class _HitlBody(BaseModel):
    hitl_event_id: int
    decision: str           # answer | reject (v0.1;approve 留后续)
    text: str | None = None


def create_app(
    db_path,
    *,
    repo_root=Path("."),
    worktrees_base=Path("data/worktrees"),
    llm_client=None,            # None → advance 内 get_llm_client()(按 TERRA_LLM_MODE)
    poll_interval: float = 0.3,
    max_rework: int = 2,
    advance_fn=None,            # 注入点(测试用假 advance);缺省真 advance
) -> FastAPI:
    db_path = Path(db_path)
    advance_fn = advance_fn or _real_advance
    app = FastAPI(title="TerraWorks View API", version="0.2")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"],
    )

    # ---- advance-runner:每 session 单飞 + dirty 标志 ----
    _lock = threading.Lock()
    _running: set[str] = set()
    _dirty: dict[str, bool] = {}

    def _run_loop(sid: str):
        while True:
            with _lock:
                if not _dirty.get(sid):
                    _running.discard(sid)      # 原子:无脏 → 退出 + 注销(防漏事件竞态)
                    return
                _dirty[sid] = False
            store = SessionStore(db_path, session_id=sid)
            try:
                advance_fn(store, llm_client=llm_client, repo_root=repo_root,
                           worktrees_base=worktrees_base, max_rework=max_rework)
            except Exception:
                traceback.print_exc()          # 不崩服务
            finally:
                store.close()

    def _ensure_advance(sid: str):
        with _lock:
            _dirty[sid] = True
            if sid in _running:
                return                          # 已在跑 → dirty 会触发再跑一轮
            _running.add(sid)
        threading.Thread(target=_run_loop, args=(sid,), daemon=True).start()

    # ---- 读端点 ----
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
            for e in _read_events(db_path, sid, cursor, None):
                await ws.send_json({"type": "event", "phase": "catchup", "event": e})
                cursor = e["event_id"]
            await ws.send_json({"type": "caught_up", "cursor": cursor})
            while True:
                for e in _read_events(db_path, sid, cursor, None):
                    await ws.send_json({"type": "event", "phase": "live", "event": e})
                    cursor = e["event_id"]
                await asyncio.sleep(poll_interval)
        except WebSocketDisconnect:
            return

    # ---- 写端点(ADR-022) ----
    @app.post("/sessions/{sid}/command")
    def post_command(sid: str, body: _CommandBody):
        if not body.text.strip():
            raise HTTPException(400, "text 不能为空")
        store = SessionStore(db_path, session_id=sid)
        try:
            ev = store.append_event(agent="user", type="user_command",
                                    payload={"text": body.text}, session_id=sid)
        finally:
            store.close()
        _ensure_advance(sid)
        return JSONResponse({"event_id": ev["event_id"]}, status_code=202)

    @app.post("/sessions/{sid}/hitl")
    def post_hitl(sid: str, body: _HitlBody):
        if body.decision not in ("answer", "reject"):
            raise HTTPException(400, "decision 仅支持 answer / reject(v0.1)")
        store = SessionStore(db_path, session_id=sid)
        try:
            target = store.get_event(body.hitl_event_id)
            if target is None or target["type"] != "hitl_request" or target["session_id"] != sid:
                raise HTTPException(400, "hitl_event_id 不是该 session 的 hitl_request")
            payload = {"decision": body.decision}
            if body.text is not None:
                payload["text"] = body.text
            ev = store.append_event(agent="user", type="hitl_response", payload=payload,
                                    parent_event_id=body.hitl_event_id, session_id=sid)
        finally:
            store.close()
        _ensure_advance(sid)
        return JSONResponse({"event_id": ev["event_id"]}, status_code=202)

    return app
