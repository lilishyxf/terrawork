"""write tool: 写入 worktree 内文件(覆盖),自动建父目录,返回 hash。"""
import hashlib
from pathlib import Path
from harness.sandbox.tools._pathguard import resolve_within, PathOutOfBounds


def write(worktree_root: Path, rel_path: str, content: str) -> dict:
    """写入 worktree 内文件(覆盖)。

    Returns:
        成功:{tool, status="ok", file, hash}
        失败:{tool, status="error", file, summary}
    """
    try:
        abs_path = resolve_within(worktree_root, rel_path)
    except PathOutOfBounds as e:
        return {"tool": "write", "status": "error", "file": rel_path, "summary": str(e)}

    abs_path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    abs_path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    return {"tool": "write", "status": "ok", "file": rel_path, "hash": sha}
