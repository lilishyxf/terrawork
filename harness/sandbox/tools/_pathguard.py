"""Path guard for sandbox tools.

substring `..` 与绝对路径 = 廉价首闸(早返回错误信息,LLM 手滑可读)
resolve() 包含校验 = 真边界(防 symlink、相对路径花式跳出)

两层叠加,缺一不可。
"""
from pathlib import Path


class PathOutOfBounds(Exception):
    """请求的路径越出 worktree 边界。"""


def resolve_within(root: Path, rel_path: str) -> Path:
    """把 rel_path 解析为 root 下的绝对路径,确保未越界。

    廉价首闸 + resolve 包含校验双重把关。

    Raises:
        PathOutOfBounds: 路径为绝对路径 / 含 `..` / resolve 后越界 / symlink 指外
    """
    p = Path(rel_path)
    # 廉价首闸 1:绝对路径 / root-anchored 路径。
    # 注意 Windows 上 Path('/etc').is_absolute() 为 False(需带盘符),但 .root 为 '\\',
    # 前导 '/' 的 rooted 路径在两平台都应拒(否则 join 时会重置到驱动器根)。
    if p.is_absolute() or p.root:
        raise PathOutOfBounds(f"absolute path not allowed: {rel_path!r}")
    # 廉价首闸 2:显式 `..` 段
    if ".." in p.parts:
        raise PathOutOfBounds(f"`..` segment not allowed: {rel_path!r}")

    # 真边界:resolve 包含校验
    root_real = root.resolve()
    target_real = (root / p).resolve()
    try:
        target_real.relative_to(root_real)
    except ValueError:
        raise PathOutOfBounds(
            f"resolved path outside worktree: {rel_path!r} → {target_real}"
        )
    return target_real
