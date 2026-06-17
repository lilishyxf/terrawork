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


# ---- M2.6-A: 真 git-merge 产物交换 helper(ADR-016) ----

def commit_worktree(
    instance_id: str,
    message: str,
    repo_root: Path = Path("."),
    base_dir: Path = Path("data/worktrees"),
) -> str:
    """把 instance worktree 的全部改动提交到其分支(npc/<slug>)。

    builder 完工后调用:`git add -A` + `git commit --allow-empty`(空产物也产生 commit,
    保证分支恒有可 merge 的提交、链路不断,ADR-016 决策 2)。

    Returns: 提交 SHA(40 位 hex)。
    """
    wt = base_dir / instance_to_slug(instance_id)
    subprocess.run(["git", "add", "-A"], cwd=str(wt), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=str(wt), check=True, capture_output=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(wt), check=True, capture_output=True, text=True,
    )
    return head.stdout.strip()


def merge_to_main(
    instance_id: str,
    repo_root: Path = Path("."),
    base_branch: str = "main",
) -> tuple[str, str | None]:
    """在 repo_root(须 base_branch 已签出且干净)把 npc/<slug> 以 --no-ff merge 进 base_branch。

    前置:repo_root 的 HEAD 是 base_branch、工作区干净(编排器主仓库恒在 main,ADR-016 决策 5)。

    Returns: (result, commit)。result ∈ {"success","conflict"};success 时 commit 为
    merge 后 HEAD 的 SHA,conflict 时为 None(已 `git merge --abort` 还原,main 不留半merge态)。
    """
    branch = branch_name(instance_id)
    proc = subprocess.run(
        ["git", "merge", "--no-ff", "-m", f"merge {branch} into {base_branch}", branch],
        cwd=str(repo_root), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        # 冲突或失败:abort 还原,交由上层落 result:conflict + HITL 兜底(ADR-016 决策 5)
        subprocess.run(["git", "merge", "--abort"], cwd=str(repo_root), capture_output=True)
        return ("conflict", None)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_root), check=True, capture_output=True, text=True,
    )
    return ("success", head.stdout.strip())


def add_verify_worktree(
    instance_id: str,
    verify_path: Path,
    repo_root: Path = Path("."),
) -> Path:
    """为 merge-then-verify 创建独立验证 worktree(ADR-016 决策 4)。

    `git worktree add --detach <verify_path> npc/<slug>`:detached 签出 builder 分支顶端,
    避开"分支已在 builder worktree 签出"的限制,得到与 builder live worktree 物理分离的
    隔离签出。verifier 在此跑验证命令,不碰 builder 的工作区。

    Returns: 验证 worktree 绝对路径。
    """
    branch = branch_name(instance_id)
    Path(verify_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(verify_path), branch],
        cwd=str(repo_root), check=True, capture_output=True,
    )
    return Path(verify_path).resolve()


def remove_worktree_path(
    worktree_path: Path,
    repo_root: Path = Path("."),
) -> None:
    """按路径强制移除一个 worktree(用于验证 worktree 用后即销,ADR-016 决策 4)。"""
    if Path(worktree_path).exists():
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=str(repo_root), check=True, capture_output=True,
        )
