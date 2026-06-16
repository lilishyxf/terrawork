"""Git worktree lifecycle for NPC sandboxing.

每个 NPC 实例(merchant#1)对应一个 worktree(data/worktrees/merchant-1)关联一个分支
(npc/merchant-1),基于 main。M1.3 阶段不做 merge——worktree 创建即用,M1.4/M2 引入仲裁后再 merge。

instance_to_slug() 是单点定义的归一化函数,目录名与分支名都从它派生,不允许散落。
"""
import re
import subprocess
from pathlib import Path


_INSTANCE_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*#[0-9]+$")


def instance_to_slug(instance_id: str) -> str:
    """归一化 NPC 实例 ID 为路径/分支安全的 slug.单点定义。

    'merchant#1' → 'merchant-1';# 是 events.schema.json 的 assignee_instance pattern
    要求,但在文件系统/git ref 上不友好,在此统一替换。
    """
    if not _INSTANCE_PATTERN.match(instance_id):
        raise ValueError(
            f"instance_id {instance_id!r} 不符合 pattern "
            f"^[a-z_][a-z0-9_]*#[0-9]+$"
        )
    return instance_id.replace("#", "-")


def worktree_path(instance_id: str, base_dir: Path = Path("data/worktrees")) -> Path:
    """data/worktrees/<slug>"""
    return base_dir / instance_to_slug(instance_id)


def branch_name(instance_id: str) -> str:
    """npc/<slug>"""
    return f"npc/{instance_to_slug(instance_id)}"


def create_worktree(
    instance_id: str,
    repo_root: Path = Path("."),
    base_branch: str = "main",
    base_dir: Path = Path("data/worktrees"),
) -> Path:
    """创建 worktree + 关联新分支(基于 base_branch)。

    Returns: worktree 绝对路径。
    Raises: FileExistsError / subprocess.CalledProcessError
    """
    wt = base_dir / instance_to_slug(instance_id)
    if wt.exists():
        raise FileExistsError(f"worktree path already exists: {wt}")
    wt.parent.mkdir(parents=True, exist_ok=True)

    branch = branch_name(instance_id)
    subprocess.run(
        ["git", "worktree", "add", str(wt), "-b", branch, base_branch],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
    )
    return wt.resolve()


def destroy_worktree(
    instance_id: str,
    repo_root: Path = Path("."),
    base_dir: Path = Path("data/worktrees"),
    delete_branch: bool = False,
) -> None:
    """销毁 worktree(强制清理);可选同时删除关联分支。"""
    wt = base_dir / instance_to_slug(instance_id)
    if wt.exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt)],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
        )
    if delete_branch:
        subprocess.run(
            ["git", "branch", "-D", branch_name(instance_id)],
            cwd=str(repo_root),
            check=False,  # 分支不存在时容错
            capture_output=True,
        )
