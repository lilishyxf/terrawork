"""worktree 生命周期测试 — tmp_path 隔离。"""
import subprocess
import pytest
from harness.sandbox import worktree as wt_mod


@pytest.fixture
def empty_repo(tmp_path):
    """最小可用 git 仓库:init + main 分支 + 一个空 commit(git worktree add 要求)。"""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    return tmp_path


def test_instance_to_slug_valid():
    assert wt_mod.instance_to_slug("merchant#1") == "merchant-1"
    assert wt_mod.instance_to_slug("tailor#42") == "tailor-42"


@pytest.mark.parametrize("bad", ["Merchant#1", "merchant", "merchant#", "1foo#1", "merchant#01a"])
def test_instance_to_slug_rejects_invalid(bad):
    with pytest.raises(ValueError):
        wt_mod.instance_to_slug(bad)


def test_branch_name():
    assert wt_mod.branch_name("merchant#1") == "npc/merchant-1"


def test_create_worktree_then_destroy(empty_repo):
    base = empty_repo / "wts"
    wt = wt_mod.create_worktree("merchant#1", repo_root=empty_repo, base_dir=base)
    assert wt.exists()
    assert (wt / ".git").exists()  # worktree 的 .git 是 file 不是 dir,但存在

    # 在 worktree 内写文件,验证可写
    (wt / "x.txt").write_text("hi")
    assert (wt / "x.txt").read_text() == "hi"

    wt_mod.destroy_worktree("merchant#1", repo_root=empty_repo, base_dir=base, delete_branch=True)
    assert not wt.exists()


def test_create_worktree_rejects_duplicate(empty_repo):
    base = empty_repo / "wts"
    wt_mod.create_worktree("merchant#1", repo_root=empty_repo, base_dir=base)
    with pytest.raises(FileExistsError):
        wt_mod.create_worktree("merchant#1", repo_root=empty_repo, base_dir=base)
