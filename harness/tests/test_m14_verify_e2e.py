"""M1.4-2 verify_executor e2e (runtime). 与 test_m14_verify.py 的静态契约物理分开。

确定性、离线(不调 LLM)。触发 m14 fixture INV-8 的 runtime 子句:
(a) verifier cwd == builder worktree (b) 真实 exit_code (c) 不写 builder worktree。
"""
from pathlib import Path

from harness.session.store import SessionStore
from harness.sandbox.verify_executor import verify_task


def _snapshot_dir(root: Path) -> dict:
    """root 下源文件的 (相对路径 -> (size, mtime_ns))。

    排除 __pycache__ 与 *.pyc——Python 执行 `import <local>` 会写字节码缓存,
    那是命令执行的副产物,不是 verifier 对 builder 源产物的写。INV-8(c) 关心的是
    verifier 是否改了 builder 的源文件,故快照排除字节码缓存。
    """
    snap = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            st = p.stat()
            snap[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return snap


def _valid_task_card(command: str, expected: dict) -> dict:
    """schema 合法 task_card(供 guide_delegate 落盘 + 传 verify_task)。"""
    return {
        "task_id": "t-login-impl",
        "assignee_role": "builder",
        "objective": "实现 login.py,暴露 login(username, password) -> dict",
        "output_format": "单文件 login.py 在 worktree 根",
        "allowed_tools": ["read", "write", "bash"],
        "boundaries": ["只用 Python 标准库"],
        "verification": [
            {"type": "machine_verifiable", "command": command, "expected": expected}
        ],
    }


def _setup(tmp_path, command, expected):
    """建 builder worktree + session.db + guide_delegate/guide_assign 链,返回锚点。"""
    repo = tmp_path / "wt"
    repo.mkdir()
    store = SessionStore(tmp_path / "session.db")
    tc = _valid_task_card(command, expected)
    de = store.append_event(
        agent="guide", type="guide_delegate",
        payload={"task_card": tc}, session_id="e2e-session",
    )
    ga = store.append_event(
        agent="guide", type="guide_assign",
        payload={"task_card_event_id": de["event_id"], "assignee_instance": "blaster#1"},
        parent_event_id=de["event_id"], session_id="e2e-session",
    )
    return repo, store, tc, ga["event_id"]


def check_inv_8_verifier_runtime(builder_worktree: Path, store: SessionStore, guide_assign_eid: int) -> None:
    """INV-8 (m14 fixture line 178-183) 三子句机械判定:
    (a) verifier cwd == builder worktree
    (b) verify_run.exit_code 与 subprocess 真实 returncode 一致(非常量)
    (c) verifier 不向 builder worktree 写源文件
    """
    before = _snapshot_dir(builder_worktree)

    # (a) cwd: 探针打印 realpath(getcwd()),output_summary 应解析为 builder_worktree
    a_card = {"task_id": "t-probe-cwd", "verification": [{
        "type": "machine_verifiable",
        "command": "python -c \"import os; print(os.path.realpath(os.getcwd()))\"",
        "expected": {"exit_code": 0},
    }]}
    a_eid = verify_task("blaster#1", a_card, builder_worktree, store, guide_assign_eid)[0]
    a_vr = store.get_event(a_eid)
    assert Path(a_vr["payload"]["output_summary"]).resolve() == builder_worktree.resolve(), \
        f"INV-8(a): verifier cwd {a_vr['payload']['output_summary']!r} != builder_worktree {builder_worktree.resolve()}"

    # (b) 真实 exit_code(非常量): 探针确定性退出 3
    b_card = {"task_id": "t-probe-exit", "verification": [{
        "type": "machine_verifiable",
        "command": "python -c \"import sys; sys.exit(3)\"",
        "expected": {"exit_code": 0},
    }]}
    b_eid = verify_task("blaster#1", b_card, builder_worktree, store, guide_assign_eid)[0]
    b_vr = store.get_event(b_eid)
    assert b_vr["payload"]["exit_code"] == 3, \
        f"INV-8(b): exit_code {b_vr['payload']['exit_code']} != real 3 (必须反映 subprocess,非常量)"
    assert b_vr["payload"]["passed"] is False

    # (c) 不写源文件
    after = _snapshot_dir(builder_worktree)
    assert before == after, \
        f"INV-8(c): builder_worktree 源文件被 verifier 改动: 新增/变化 {set(after.items()) ^ set(before.items())}"


def test_verify_executor_machine_verifiable_passes_runtime(tmp_path):
    """machine_verifiable 通过路径 + INV-8 runtime。"""
    cmd = "python -c \"import login; print('OK')\""
    repo, store, tc, ga = _setup(tmp_path, cmd, {"exit_code": 0, "stdout_contains": "OK"})
    (repo / "login.py").write_text("def login(u, p):\n    return {'ok': True}\n", encoding="utf-8")

    src_before = (repo / "login.py").read_text(encoding="utf-8")
    event_ids = verify_task("blaster#1", tc, repo, store, ga)

    assert len(event_ids) == 1
    vr = store.get_event(event_ids[0])
    assert vr["type"] == "verify_run"
    assert vr["payload"]["command"] == tc["verification"][0]["command"]  # INV-2 runtime: 逐字
    assert vr["payload"]["exit_code"] == 0
    assert vr["payload"]["passed"] is True
    assert vr["parent_event_id"] == ga
    # (c) 真实验证命令未改源
    assert (repo / "login.py").read_text(encoding="utf-8") == src_before

    # INV-8 三子句
    check_inv_8_verifier_runtime(repo, store, ga)


def test_verify_executor_machine_verifiable_fails_runtime(tmp_path):
    """machine_verifiable 失败路径: exit_code 0 但 stdout 不含 OK → passed False。"""
    cmd = "python -c \"import login; print('BAD')\""
    repo, store, tc, ga = _setup(tmp_path, cmd, {"exit_code": 0, "stdout_contains": "OK"})
    (repo / "login.py").write_text("def login(u, p):\n    return {'ok': False}\n", encoding="utf-8")

    event_ids = verify_task("blaster#1", tc, repo, store, ga)
    vr = store.get_event(event_ids[0])
    assert vr["payload"]["passed"] is False
    assert vr["payload"]["exit_code"] == 0  # 命令成功,仅 stdout 不匹配
