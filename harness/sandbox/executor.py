"""NPC execution function. Pure function per ADR-009 / ADR-012.

M1.3-3:同进程实现,scripted_actions 测试模式(绕过 LLM,顺序执行预定 tool 调用)。
M1.3-4:接真实 LLM iterative tool calling(scripted_actions=None 时走真实路径)。
M2:    包装为 subprocess + JSON-RPC,接口签名不变(数据参数 JSON 化、基础设施句柄子进程端各自重建)。

LLM 真实路径(scripted_actions=None)在 M1.3-3 不实现,留 M1.3-4。

SessionStore API(ground truth,见 harness/session/store.py):
- append_event(*, agent, type, payload, parent_event_id=None, session_id=None, ts=None) -> dict
- get_event(event_id) -> dict | None
"""
import re
from datetime import datetime
from pathlib import Path

from harness.sandbox.worktree import create_worktree
from harness.sandbox.tools import read, write, bash


_INSTANCE_RE = re.compile(r"^([a-z_][a-z0-9_]*)#[0-9]+$")


def _role_from_instance(npc_instance_id: str) -> str:
    """'merchant#1' → 'merchant'(同时校验实例 ID 格式)。"""
    m = _INSTANCE_RE.match(npc_instance_id)
    if not m:
        raise ValueError(f"invalid npc_instance_id: {npc_instance_id!r}")
    return m.group(1)


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def execute_npc(
    npc_instance_id: str,
    task_card: dict,
    session_store,
    guide_assign_event_id: int,
    *,
    repo_root: Path = Path("."),
    worktrees_base: Path = Path("data/worktrees"),
    llm_client=None,
    scripted_actions: list[dict] | None = None,
    max_iterations: int = 10,
) -> None:
    """执行单 NPC 任务。M1.3 同进程实现(ADR-012)。

    Args:
        npc_instance_id: 形如 "merchant#1"
        task_card: 任务卡 dict(从 guide_delegate.payload.task_card)
        session_store: SessionStore 实例(写事件)
        guide_assign_event_id: trigger 事件 ID(review_request 的 parent)
        repo_root: 主仓库根(默认 ".",测试时传 tmp_path)
        worktrees_base: worktree 目录基(默认 data/worktrees,测试时传 tmp_path)
        llm_client: M1.3-4 才用;M1.3-3 不传(scripted 模式)
        scripted_actions: 测试用 list of {tool, params, task_id};传入则绕过 LLM
        max_iterations: 真实 LLM 模式下的轮次上限(scripted 模式忽略)

    Returns:
        None。产出全部通过 session_store 写事件(ADR-012)。
    """
    # 校验实例 ID 格式;role 留 M1.3-4 LLM context assembly 用
    _role_from_instance(npc_instance_id)

    trigger = session_store.get_event(guide_assign_event_id)
    if trigger is None:
        raise ValueError(f"guide_assign event {guide_assign_event_id} not found")
    session_id = trigger["session_id"]

    # 创建 worktree
    wt_path = create_worktree(
        npc_instance_id,
        repo_root=repo_root,
        base_dir=worktrees_base,
    )

    if scripted_actions is None:
        raise NotImplementedError(
            "real LLM execution not yet implemented; pass scripted_actions for M1.3-3 testing"
        )

    # 收集 write 产出的 files(供 review_request.artifact 用)
    written_files: list[str] = []
    last_event_id = guide_assign_event_id

    # Scripted 模式:顺序执行预定 actions
    for action in scripted_actions:
        tool_name = action["tool"]
        full_params = dict(action["params"])
        task_id = action.get("task_id")

        # 落 tool_intent(不含大字段 content)
        intent_params = {k: v for k, v in full_params.items() if k != "content"}
        intent_payload = {"tool": tool_name, "params": intent_params}
        if task_id:
            intent_payload["task_id"] = task_id
        intent = session_store.append_event(
            agent=npc_instance_id,
            type="tool_intent",
            payload=intent_payload,
            parent_event_id=last_event_id,
            session_id=session_id,
            ts=_now_iso(),
        )
        intent_id = intent["event_id"]

        # 调实际工具(返回 dict 已符合 p_tool_done schema 形态)
        if tool_name == "read":
            result = read(wt_path, full_params["path"])
        elif tool_name == "write":
            result = write(wt_path, full_params["path"], full_params.get("content", ""))
            if result["status"] == "ok":
                written_files.append(full_params["path"])
        elif tool_name == "bash":
            result = bash(wt_path, full_params["cmd"])
        else:
            result = {"tool": tool_name, "status": "error", "summary": f"unknown tool: {tool_name}"}

        # 落 tool_done,parent 链回配对的 intent(WAL 配对)
        done = session_store.append_event(
            agent=npc_instance_id,
            type="tool_done",
            payload=result,
            parent_event_id=intent_id,
            session_id=session_id,
            ts=_now_iso(),
        )
        last_event_id = done["event_id"]

    # 完成:落 review_request,parent 链回 trigger(guide_assign)
    session_store.append_event(
        agent=npc_instance_id,
        type="review_request",
        payload={
            "task_id": task_card["task_id"],
            "reviewer": "tailor",  # M1.3 hardcode;M1.4/M2 视 verifier 引入再调整
            "artifact": {
                "files": written_files,
                "worktree": str(wt_path),
            },
        },
        parent_event_id=guide_assign_event_id,
        session_id=session_id,
        ts=_now_iso(),
    )
