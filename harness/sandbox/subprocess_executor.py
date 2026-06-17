"""NPC 执行的子进程包装(ADR-017,收尾 ADR-012 进程隔离)。

execute_npc 保持进程内纯函数;本模块把它包成独立子进程:
- parent:run_npc_subprocess(...) 用 Popen([sys.executable, -m 本模块]) + stdin JSON
  传全部数据参数;阻塞等待,非零退出 → NpcSubprocessError。
- child:main() 读 stdin JSON → 重建 SessionStore(db_path, session_id) + get_llm_client()
  → 调 execute_npc → 成功 exit 0、异常把 traceback 写 stdout 并 exit 1。

退化版 JSON-RPC:一请求(stdin)一响应(stdout + 退出码);NPC 产出的事件由 child 端
SessionStore 直接写同一 SQLite(WAL 多写者),不经管道回传(ADR-017 决策 2)。

基础设施句柄(session_store/llm_client)不跨进程传递,子进程端各自重建(ADR-012)。
llm_client 不传——child 一律 get_llm_client();scripted_actions 存在时 execute_npc
绕过 LLM,故子进程测试可用 scripted_actions 保持确定。
"""
import sys
import json
import subprocess
import traceback
from pathlib import Path


class NpcSubprocessError(RuntimeError):
    """子进程 NPC 执行失败(非零退出);携带 child 上报的错误详情。"""


def run_npc_subprocess(
    *,
    npc_instance_id: str,
    task_card: dict,
    db_path: str,
    session_id: str,
    guide_assign_event_id: int,
    repo_root: str,
    worktrees_base: str,
    scripted_actions: list[dict] | None = None,
    max_iterations: int = 10,
    reuse_worktree: bool = False,
    rework_notes: str | None = None,
    timeout: float | None = None,
) -> None:
    """parent 侧:spawn 子进程执行一个 NPC。事件经 SQLite 旁路落盘,本函数无返回值。

    Raises:
        NpcSubprocessError: child 非零退出(崩溃/执行异常)。主进程不受影响(隔离)。
    """
    params = {
        "npc_instance_id": npc_instance_id,
        "task_card": task_card,
        "db_path": db_path,
        "session_id": session_id,
        "guide_assign_event_id": guide_assign_event_id,
        "repo_root": repo_root,
        "worktrees_base": worktrees_base,
        "scripted_actions": scripted_actions,
        "max_iterations": max_iterations,
        "reuse_worktree": reuse_worktree,
        "rework_notes": rework_notes,
    }
    proc = subprocess.run(
        [sys.executable, "-m", "harness.sandbox.subprocess_executor"],
        input=json.dumps(params),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        detail = proc.stdout.strip() or proc.stderr.strip() or "(no output)"
        raise NpcSubprocessError(
            f"NPC {npc_instance_id} subprocess exited {proc.returncode}: {detail}"
        )


def main() -> int:
    """child 侧入口:读 stdin JSON → 重建基础设施 → execute_npc → 上报状态。"""
    # 延迟 import:仅子进程端需要,避免 parent import 本模块时拉起重依赖。
    from harness.session.store import SessionStore
    from harness.sandbox.executor import execute_npc

    try:
        params = json.loads(sys.stdin.read())
        store = SessionStore(params["db_path"], session_id=params["session_id"])
        # llm_client 不传:execute_npc 在 scripted 模式忽略它、真实模式自行 get_llm_client()。
        execute_npc(
            params["npc_instance_id"],
            params["task_card"],
            store,
            params["guide_assign_event_id"],
            repo_root=Path(params["repo_root"]),
            worktrees_base=Path(params["worktrees_base"]),
            llm_client=None,
            scripted_actions=params.get("scripted_actions"),
            max_iterations=params.get("max_iterations", 10),
            reuse_worktree=params.get("reuse_worktree", False),
            rework_notes=params.get("rework_notes"),
        )
    except Exception:
        sys.stdout.write(json.dumps({"status": "error", "error": traceback.format_exc()}))
        sys.stdout.flush()
        return 1
    sys.stdout.write(json.dumps({"status": "ok"}))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
