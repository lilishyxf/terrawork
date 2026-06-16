"""bash tool: 在 worktree cwd 执行 shell 命令。

策略 B(ADR-013 候选):denylist = 防 LLM 事故护栏,**不是** OS 沙箱。
真隔离归 M2(ADR-012 子进程化 + 容器/seccomp 一并谈)。

status / exit_code 二分:
- status="error":工具调用本身失败(denylist 命中、超时、进程启动失败)
- status="ok" + exit_code=N:命令成功启动,N 为命令本身返回码(测试 fail → 1,不影响 status)
"""
import re
import subprocess
from pathlib import Path


# Denylist 初版 5 条(M1.3 单 NPC 本机 + 全程事件流可追溯下足够;不当 OS 沙箱用)
_DENY_PATTERNS = [
    re.compile(r"\brm\s+-rf\s+[/~]"),         # rm -rf / 或 rm -rf ~
    re.compile(r"\bsudo\b"),                   # sudo
    re.compile(r"\bcurl\b.*\|\s*sh\b"),        # curl ... | sh
    re.compile(r"\bwget\b.*\|\s*sh\b"),        # wget ... | sh
    re.compile(r"\.\."),                       # `..` 越界(粗暴但有效,M1.3 单 NPC 阶段够用)
]


def _check_denylist(cmd: str) -> str | None:
    """命中返回拒绝原因,未命中返回 None。"""
    for pat in _DENY_PATTERNS:
        if pat.search(cmd):
            return f"denied by safety denylist (pattern: {pat.pattern!r})"
    return None


def bash(worktree_root: Path, cmd: str, *, timeout: int = 60) -> dict:
    """在 worktree_root 内执行 shell 命令。

    Returns:
        denylist 命中:{tool, status="error", summary}
        超时:        {tool, status="error", summary}
        正常执行:    {tool, status="ok", exit_code, summary}(exit_code 可为任意整数)
    """
    denied = _check_denylist(cmd)
    if denied:
        return {"tool": "bash", "status": "error", "summary": denied}

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=str(worktree_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",  # 非 UTF-8 控制台(如中文 Windows GBK 输出)不崩溃
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"tool": "bash", "status": "error", "summary": f"timeout after {timeout}s"}

    out = (result.stdout + result.stderr).strip()
    summary = (out[:500] + "...") if len(out) > 500 else out
    return {
        "tool": "bash",
        "status": "ok",
        "exit_code": result.returncode,
        "summary": summary or "(no output)",
    }
