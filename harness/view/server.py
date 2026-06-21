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
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
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


class _Attachment(BaseModel):
    name: str
    content: str


class _CommandBody(BaseModel):
    text: str
    model: str | None = None   # 模型选择(全局覆盖);空/None=各角色默认
    attachments: list[_Attachment] | None = None   # 可选:文本/代码文件当上下文


class _HitlBody(BaseModel):
    hitl_event_id: int
    decision: str           # answer | reject (v0.1;approve 留后续)
    text: str | None = None


class _WorkspaceBody(BaseModel):
    path: str               # 目标项目仓库的绝对路径


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
    # 工作区(目标仓库)可运行时切换:默认沙箱,用户可指向真实项目(merge 进其 main)。
    _ws = {"root": Path(repo_root).resolve()}
    _PRODUCT_ROOT = Path(__file__).resolve().parents[2]
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
        fails = 0
        while True:
            with _lock:
                if not _dirty.get(sid):
                    _running.discard(sid)      # 原子:无脏 → 退出 + 注销(防漏事件竞态)
                    return
                _dirty[sid] = False
            store = SessionStore(db_path, session_id=sid)
            try:
                advance_fn(store, llm_client=llm_client, repo_root=_ws["root"],
                           worktrees_base=worktrees_base, max_rework=max_rework)
                fails = 0
            except Exception:
                traceback.print_exc()          # 不崩服务
                fails += 1
                # 多半是网络抖断 LLM 调用(如手机热点)→ 标脏 + 退避重试,网络恢复即自愈;
                # 连续失败 >5 才放弃(留可恢复:下次 /command 或重启续作),避免对真错误空转。
                if fails <= 5:
                    with _lock:
                        _dirty[sid] = True
                    time.sleep(min(5 * fails, 30))
            finally:
                store.close()

    def _ensure_advance(sid: str):
        with _lock:
            _dirty[sid] = True
            if sid in _running:
                return                          # 已在跑 → dirty 会触发再跑一轮
            _running.add(sid)
        threading.Thread(target=_run_loop, args=(sid,), daemon=True).start()

    @app.on_event("startup")
    def _resume_pending():
        """崩溃/重启续作(铁律:恢复状态不恢复思维)。遍历已有 session 各触发一次 advance:
        有半截任务的会从日志续作(finish_build 等),已静止的快速空跑返回。
        修复:此前 advance 只在 POST 触发,强杀重启后半截任务会永久卡住。"""
        import sqlite3
        if not Path(db_path).exists():
            return
        try:
            conn = sqlite3.connect(str(db_path))
            sids = [r[0] for r in conn.execute("SELECT DISTINCT session_id FROM events")]
            conn.close()
        except Exception:
            return
        for sid in sids:
            _ensure_advance(sid)

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
        # 模型选择(全局覆盖):设/清 TERRA_MODEL_OVERRIDE,advance 各 LLM 调用读它
        os.environ["TERRA_MODEL_OVERRIDE"] = (body.model or "").strip()
        payload = {"text": body.text}
        if body.attachments:
            payload["attachments"] = [{"name": a.name, "content": a.content} for a in body.attachments]
        store = SessionStore(db_path, session_id=sid)
        try:
            ev = store.append_event(agent="user", type="user_command",
                                    payload=payload, session_id=sid)
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

    # ---- 工作区(目标仓库):切换 + 文件浏览 + 动态预览 ----
    _SKIP = ("/.git/", "__pycache__")

    def _git(args, cwd):
        # 显式 utf-8 解码:中文 Windows 默认 GBK 会在 git 输出含 UTF-8 时炸
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    @app.get("/workspace")
    def get_workspace():
        root = _ws["root"]
        return {"path": str(root), "is_git": (root / ".git").is_dir()}

    def _apply_workspace(path_str: str):
        """切到目标仓库(NPC 在此 git 改代码、merge 进 main)。规整到 main、要求干净;
        拒绝指向 TerraWorks 自身。非 git 目录自动 init。"""
        p = Path(path_str).expanduser().resolve()
        if not p.is_dir():
            raise HTTPException(400, f"目录不存在:{p}")
        if p == _PRODUCT_ROOT:
            raise HTTPException(400, "不能指向 TerraWorks 产品仓库自身(会污染本项目)")
        if not (p / ".git").is_dir():
            _git(["init", "-b", "main"], p)
            _git(["config", "user.name", "TerraWorks"], p)
            _git(["config", "user.email", "agent@terraworks.local"], p)
            _git(["add", "-A"], p)
            _git(["commit", "-m", "terraworks: 初始化工作区", "--allow-empty"], p)
        else:
            if _git(["status", "--porcelain"], p).stdout.strip():
                raise HTTPException(409, "目标仓库有未提交改动,请先提交或暂存再切换(NPC 需干净的 main)")
            if _git(["rev-parse", "--verify", "main"], p).returncode != 0:
                _git(["branch", "main"], p)        # 从当前 HEAD 建 main(不动原分支)
            if _git(["checkout", "main"], p).returncode != 0:
                raise HTTPException(409, "无法切到 main 分支")
        _ws["root"] = p
        return {"path": str(p), "is_git": True}

    @app.post("/workspace")
    def set_workspace(body: _WorkspaceBody):
        return _apply_workspace(body.path)

    @app.post("/workspace/pick")
    def pick_workspace():
        """本机弹出系统原生文件夹对话框(后端进程,子进程跑 tkinter),选中即切换。
        浏览器拿不到服务器端路径,故由本地后端代开原生框——单机壳的正解。"""
        code = (
            "import tkinter as tk;from tkinter import filedialog;"
            "r=tk.Tk();r.withdraw();r.attributes('-topmost',True);"
            "p=filedialog.askdirectory(title='选择 NPC 要干活的项目文件夹');print(p)"
        )
        try:
            proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=300)
        except Exception as e:
            raise HTTPException(500, f"无法打开文件夹对话框:{e}")
        chosen = (proc.stdout or "").strip().splitlines()
        path = chosen[-1].strip() if chosen else ""
        if not path:
            return {"path": str(_ws["root"]), "cancelled": True}   # 用户取消
        return _apply_workspace(path)

    @app.get("/workspace/tree")
    def workspace_tree():
        root = _ws["root"]
        files = []
        if root.is_dir():
            for fp in sorted(root.rglob("*")):
                if not fp.is_file():
                    continue
                rel = fp.relative_to(root).as_posix()
                if any(s in f"/{rel}" for s in _SKIP) or rel.endswith(".pyc"):
                    continue
                files.append(rel)
        return {"files": files}

    @app.get("/workspace/file")
    def workspace_file(path: str):
        root = _ws["root"].resolve()
        target = (root / path).resolve()
        if not str(target).startswith(str(root)) or not target.is_file():
            raise HTTPException(404, "not found")
        try:
            content = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            content = "(二进制或不可读文件)"
        return {"path": path, "content": content}

    @app.get("/workspace/raw/{path:path}")
    def workspace_raw(path: str):
        """按路径返回工作区文件(供预览 iframe 当场运行 index.html)。"""
        root = _ws["root"].resolve()
        target = (root / path).resolve()
        if not str(target).startswith(str(root)) or not target.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(str(target))

    return app
