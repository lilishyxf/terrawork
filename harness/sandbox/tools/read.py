"""read tool: 读取 worktree 内文件,返回 hash + summary。"""
import hashlib
from pathlib import Path
from harness.sandbox.tools._pathguard import resolve_within, PathOutOfBounds


def read(worktree_root: Path, rel_path: str, *, max_bytes: int = 100_000) -> dict:
    """读取 worktree 内的文件。

    Returns(可作 tool_done.payload):
        成功:{tool, status="ok", file, hash, summary}
        失败:{tool, status="error", file, summary}
    """
    try:
        abs_path = resolve_within(worktree_root, rel_path)
    except PathOutOfBounds as e:
        return {"tool": "read", "status": "error", "file": rel_path, "summary": str(e)}

    if not abs_path.exists():
        return {"tool": "read", "status": "error", "file": rel_path, "summary": "file not found"}
    if not abs_path.is_file():
        return {"tool": "read", "status": "error", "file": rel_path, "summary": "not a regular file"}

    data = abs_path.read_bytes()
    if len(data) > max_bytes:
        return {
            "tool": "read", "status": "error", "file": rel_path,
            "summary": f"file too large: {len(data)} bytes (limit {max_bytes})",
        }

    text = data.decode("utf-8", errors="replace")
    sha = hashlib.sha256(data).hexdigest()
    summary = text[:200] + ("..." if len(text) > 200 else "")
    return {"tool": "read", "status": "ok", "file": rel_path, "hash": sha, "summary": summary}
