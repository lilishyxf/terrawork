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


# ---- M2.6-A: 真 git-merge helper(ADR-016) ----

_HASH_RE = __import__("re").compile(r"^[0-9a-f]{40}$")


def test_commit_worktree_returns_real_hash(empty_repo):
    base = empty_repo / "wts"
    wt = wt_mod.create_worktree("merchant#1", repo_root=empty_repo, base_dir=base)
    (wt / "a.py").write_text("X = 1\n")
    sha = wt_mod.commit_worktree("merchant#1", "build", repo_root=empty_repo, base_dir=base)
    assert _HASH_RE.match(sha), sha
    # 文件已提交:rev-parse 的 tree 含 a.py
    ls = subprocess.run(["git", "ls-files"], cwd=wt, check=True, capture_output=True, text=True)
    assert "a.py" in ls.stdout


def test_commit_worktree_allows_empty(empty_repo):
    """空产物也产生 commit(--allow-empty),保证分支恒有可 merge 提交。"""
    base = empty_repo / "wts"
    wt_mod.create_worktree("merchant#1", repo_root=empty_repo, base_dir=base)
    sha = wt_mod.commit_worktree("merchant#1", "empty build", repo_root=empty_repo, base_dir=base)
    assert _HASH_RE.match(sha)


def test_merge_to_main_advances_main(empty_repo):
    base = empty_repo / "wts"
    wt = wt_mod.create_worktree("merchant#1", repo_root=empty_repo, base_dir=base)
    (wt / "login.py").write_text("def login(): return True\n")
    wt_mod.commit_worktree("merchant#1", "impl login", repo_root=empty_repo, base_dir=base)

    result, commit = wt_mod.merge_to_main("merchant#1", repo_root=empty_repo)
    assert result == "success" and _HASH_RE.match(commit)
    # main 真实含产物
    assert (empty_repo / "login.py").exists(), "merge 后 main 工作树应含 login.py"


def test_dep_via_main_then_branch_from_main(empty_repo):
    """ADR-016 决策 3:依赖卡 merge 进 main 后,后续卡从 main 切即自带依赖产物。"""
    base = empty_repo / "wts"
    # 测试卡:写 test_x.py、提交、merge 进 main
    wt1 = wt_mod.create_worktree("merchant#1", repo_root=empty_repo, base_dir=base)
    (wt1 / "test_x.py").write_text("def test_x(): assert True\n")
    wt_mod.commit_worktree("merchant#1", "tests", repo_root=empty_repo, base_dir=base)
    assert wt_mod.merge_to_main("merchant#1", repo_root=empty_repo)[0] == "success"

    # 实现卡:从 main 切的新 worktree 应已含 test_x.py(无需共享目录)
    wt2 = wt_mod.create_worktree("merchant#2", repo_root=empty_repo, base_dir=base)
    assert (wt2 / "test_x.py").exists(), "从 main 切的实现卡 worktree 应自带已 merge 的测试"


def test_verify_worktree_is_isolated(empty_repo):
    """ADR-016 决策 4:验证 worktree 是 builder 分支的独立 detached 签出,≠ builder live worktree。"""
    base = empty_repo / "wts"
    wt = wt_mod.create_worktree("merchant#1", repo_root=empty_repo, base_dir=base)
    (wt / "login.py").write_text("V = 1\n")
    wt_mod.commit_worktree("merchant#1", "impl", repo_root=empty_repo, base_dir=base)

    vpath = base / "verify-merchant-1"
    vwt = wt_mod.add_verify_worktree("merchant#1", vpath, repo_root=empty_repo)
    assert vwt.exists() and vwt != wt.resolve(), "验证 worktree 须与 builder live worktree 物理分离"
    assert (vwt / "login.py").read_text() == "V = 1\n", "验证 worktree 应含 builder 已提交产物"

    wt_mod.remove_worktree_path(vwt, repo_root=empty_repo)
    assert not vwt.exists()


def test_merge_to_main_conflict_aborts_clean(empty_repo):
    """冲突时返回 ('conflict', None) 且 main 被 abort 还原(不留半 merge 态)。"""
    base = empty_repo / "wts"
    # main 上先有 c.txt=main
    (empty_repo / "c.txt").write_text("main\n")
    subprocess.run(["git", "add", "-A"], cwd=empty_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base c"], cwd=empty_repo, check=True, capture_output=True)
    # 分支改同一文件冲突
    wt = wt_mod.create_worktree("merchant#1", repo_root=empty_repo, base_dir=base)
    (wt / "c.txt").write_text("branch\n")
    wt_mod.commit_worktree("merchant#1", "branch c", repo_root=empty_repo, base_dir=base)
    # main 再改同一文件
    (empty_repo / "c.txt").write_text("main2\n")
    subprocess.run(["git", "commit", "-am", "main c2"], cwd=empty_repo, check=True, capture_output=True)

    result, commit = wt_mod.merge_to_main("merchant#1", repo_root=empty_repo)
    assert result == "conflict" and commit is None
    # abort 已还原:无 merge 残留(MERGE_HEAD 清除)、无冲突/暂存的已跟踪改动
    # (忽略未跟踪 '??':测试把 worktree base 建在 repo 内,真实用 gitignored data/worktrees)
    assert not (empty_repo / ".git" / "MERGE_HEAD").exists(), "abort 后不应留 MERGE_HEAD"
    status = subprocess.run(["git", "status", "--porcelain"], cwd=empty_repo,
                            check=True, capture_output=True, text=True)
    tracked = [l for l in status.stdout.splitlines() if not l.startswith("??")]
    assert tracked == [], f"abort 后不应留已跟踪改动,实际: {tracked}"
    assert (empty_repo / "c.txt").read_text() == "main2\n"
