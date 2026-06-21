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
import json
from datetime import datetime
from pathlib import Path

from harness.sandbox.worktree import create_worktree, instance_to_slug, commit_worktree
from harness.sandbox.tools import read, write, bash
from harness.context.assemble import assemble_context_for_npc, load_role_frontmatter
from harness.llm import get_llm_client


_INSTANCE_RE = re.compile(r"^([a-z_][a-z0-9_]*)#[0-9]+$")


# OpenAI/LiteLLM tool schema format. Per-tool descriptions inform the LLM
# of usage and constraints. Source of truth for tool semantics is in
# harness/sandbox/tools/*.py — keep this schema in sync if tool signatures change.
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a file in the worktree. Path must be relative to the worktree root; absolute paths or paths escaping the worktree (e.g., '../') are rejected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path within the worktree"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write",
            "description": "Write content to a file in the worktree. Creates parent directories as needed. Absolute paths are rejected. Overwrites existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path within the worktree"},
                    "content": {"type": "string", "description": "File content (UTF-8 text)"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a shell command in the worktree. Returns {status, exit_code, summary}. Note: bash is for development feedback during iteration; it is NOT the verification gate (that is the verifier's job). Subject to a denylist (no 'rm -rf /', 'sudo', 'curl|sh', paths containing '..').",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Shell command to execute"},
                },
                "required": ["cmd"],
            },
        },
    },
]


def _dispatch_tool(tool_name: str, params: dict, wt_path):
    """Route tool name to actual implementation. Returns result dict (matches p_tool_done schema)."""
    if tool_name == "read":
        return read(wt_path, params["path"])
    elif tool_name == "write":
        return write(wt_path, params["path"], params.get("content", ""))
    elif tool_name == "bash":
        return bash(wt_path, params["cmd"])
    else:
        return {"tool": tool_name, "status": "error", "summary": f"unknown tool: {tool_name}"}


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
    max_iterations: int = 25,
    reuse_worktree: bool = False,
    rework_notes: str | None = None,
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
    # 校验实例 ID 格式 + 取 role(LLM 路径用于 context assembly 与读 model)
    role = _role_from_instance(npc_instance_id)

    trigger = session_store.get_event(guide_assign_event_id)
    if trigger is None:
        raise ValueError(f"guide_assign event {guide_assign_event_id} not found")
    session_id = trigger["session_id"]

    # worktree 按实例隔离(ADR-016 撤销 ADR-015 共享模型):merchant#1 → merchant-1。
    # 依赖卡的产物可见性靠"依赖先 merge 进 main + 本卡 worktree 从 main 切"实现(编排器
    # 的 depends_on 门保证次序),不再靠共享目录。
    if reuse_worktree:
        wt_path = (worktrees_base / instance_to_slug(npc_instance_id)).resolve()
        if not wt_path.is_dir():
            raise FileNotFoundError(
                f"reuse_worktree=True but worktree missing: {wt_path}"
            )
    else:
        wt_path = create_worktree(
            npc_instance_id,
            repo_root=repo_root,
            base_dir=worktrees_base,
        )

    # 收集 write 产出的 files(供 review_request.artifact 用)
    written_files: list[str] = []
    last_event_id = guide_assign_event_id

    if scripted_actions is not None:
        # ---- M1.3-3 scripted 路径:顺序执行预定 actions(绕过 LLM) ----
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

            result = _dispatch_tool(tool_name, full_params, wt_path)
            if tool_name == "write" and result.get("status") == "ok":
                written_files.append(full_params["path"])

            done = session_store.append_event(
                agent=npc_instance_id,
                type="tool_done",
                payload=result,
                parent_event_id=intent_id,
                session_id=session_id,
                ts=_now_iso(),
            )
            last_event_id = done["event_id"]
    else:
        # ---- M1.3-4 真实 LLM iterative tool calling 路径 ----
        if llm_client is None:
            llm_client = get_llm_client()

        role_frontmatter = load_role_frontmatter(role)
        role_model = role_frontmatter.get("model")
        if not role_model:
            raise ValueError(f"role '{role}' frontmatter missing 'model' field")
        from harness.llm import resolve_model
        role_model = resolve_model(role_model)  # TERRA_MODEL_OVERRIDE 全局覆盖

        messages = assemble_context_for_npc(role, task_card)
        if rework_notes:
            # 返工:把上轮 reject 的整改要点注入 builder context(M2.2b)
            messages.append({
                "role": "user",
                "content": f"上一轮审查被退回(reject),整改要点:\n{rework_notes}\n请据此修正后重做。",
            })

        completed_normally = False
        bash_cmds: list[str] = []          # 收敛护栏:近期 bash 命令(防死循环)
        warned_budget = False
        warned_repeat = False
        for _iteration in range(max_iterations):
            # 收敛护栏 B1:接近迭代上限 → 提醒收手(只提醒一次)
            if not warned_budget and max_iterations - _iteration <= 5:
                messages.append({
                    "role": "user",
                    "content": "⏱ 已接近工具调用上限。若实现已完成,请**立刻停止调用任何工具**,"
                               "只回一段简短总结即可;不要再反复 bash 自测。",
                })
                warned_budget = True
            # 收敛护栏 B2:连续 3 次相同 bash → 提醒别打转(只提醒一次)
            if not warned_repeat and len(bash_cmds) >= 3 and len(set(bash_cmds[-3:])) == 1:
                messages.append({
                    "role": "user",
                    "content": "你在重复执行同一条命令。若实现已完成就**停手交付**(不再调工具);"
                               "若没完成,换个思路,别原地打转。",
                })
                warned_repeat = True
            message = llm_client.complete_with_tools(
                model=role_model,
                messages=messages,
                tools=TOOLS_SCHEMA,
            )
            tool_calls = getattr(message, "tool_calls", None) or []

            if not tool_calls:
                # 无 tool_calls → 完成信号
                completed_normally = True
                break

            # 保留带 tool_calls 的 assistant message(OpenAI 协议要求,关联 tool 结果)
            messages.append({
                "role": "assistant",
                "content": getattr(message, "content", None) or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                tool_name = tc.function.name
                try:
                    full_params = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    full_params = {}

                # 落 tool_intent(剥离大字段 content)
                intent_params = {k: v for k, v in full_params.items() if k != "content"}
                intent = session_store.append_event(
                    agent=npc_instance_id,
                    type="tool_intent",
                    payload={
                        "tool": tool_name,
                        "params": intent_params,
                        "task_id": task_card["task_id"],
                    },
                    parent_event_id=last_event_id,
                    session_id=session_id,
                )
                intent_id = intent["event_id"]

                result = _dispatch_tool(tool_name, full_params, wt_path)
                if tool_name == "write" and result.get("status") == "ok" and "path" in full_params:
                    written_files.append(full_params["path"])
                if tool_name == "bash":
                    bash_cmds.append(str(full_params.get("cmd", "")))  # 收敛护栏 B2 检测用

                done = session_store.append_event(
                    agent=npc_instance_id,
                    type="tool_done",
                    payload=result,
                    parent_event_id=intent_id,
                    session_id=session_id,
                )
                last_event_id = done["event_id"]

                # 把 tool 结果作为 tool message 喂回(OpenAI 协议)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result),
                })

        if not completed_normally:
            raise RuntimeError(
                f"NPC {npc_instance_id} exceeded max_iterations={max_iterations} "
                f"without producing a completion signal (no-tool-calls response)"
            )

    # 完工提交:把产物提交到本实例分支(npc/<slug>),产物=已提交分支态供 Guide 仲裁 merge
    # (ADR-016 决策 2)。空产物 --allow-empty 也提交,保证分支恒有可 merge 提交、链路不断。
    commit_worktree(
        npc_instance_id,
        f"{npc_instance_id}: {task_card['task_id']}",
        repo_root=repo_root,
        base_dir=worktrees_base,
    )

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
