"""M2.3 崩溃重启无缝续作(§12 M2 验收)。

approach B:advance 事件驱动 + 幂等,重启续作 = reopen db 后再 advance()。
构建中途崩溃(builder 已派、无 review_request)由 finish_build 阶段续作;其余崩溃点 advance 自然续作。
离线确定性。
"""
import json
import subprocess
from pathlib import Path

from harness.session.store import SessionStore
from harness.sandbox.worktree import create_worktree
from harness.orchestrator import advance

_CARD = {
    "task_id": "t-login-impl", "assignee_role": "builder",
    "objective": "实现 login.py", "output_format": "login.py",
    "allowed_tools": ["read", "write", "bash"], "boundaries": ["只用标准库"],
    "verification": [{"type": "machine_verifiable",
                      "command": "python -c \"import login; print('OK')\"",
                      "expected": {"exit_code": 0, "stdout_contains": "OK"}}],
}
_GUIDE_JSON = json.dumps({"thinking": "单卡", "tasks": [_CARD]}, ensure_ascii=False)
_BUILDER_ACTIONS = {"t-login-impl": [
    {"tool": "write", "params": {"path": "login.py", "content": "def login(u, p):\n    return {'ok': True}\n"},
     "task_id": "t-login-impl"}
]}


class _StubLLM:
    def complete(self, model, messages, **kwargs):
        system = messages[0]["content"] if messages else ""
        if "裁缝" in system or "Tailor" in system:
            return '{"verdict": "pass", "notes": "ok"}'
        return _GUIDE_JSON


def _git_init_repo(repo: Path):
    for c in (["git", "init", "-b", "main"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"], ["git", "commit", "--allow-empty", "-m", "init"]):
        subprocess.run(c, cwd=repo, check=True, capture_output=True)


def test_resume_after_crash_mid_build(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir(); _git_init_repo(repo)
    db = tmp_path / "session.db"
    wtb = tmp_path / "worktrees"

    # --- 进程1:跑到"构建中途"被强杀 ---
    # 持久化:分解 + 派 builder + worktree 已建 + 一条 unpaired tool_intent(无 tool_done/review_request)
    store = SessionStore(db)
    cmd = store.append_event(agent="user", type="user_command",
                             payload={"text": "做个登录"}, session_id="resume")
    th = store.append_event(agent="guide", type="guide_think", parent_event_id=cmd["event_id"],
                            session_id="resume", payload={"text": "单卡"})
    de = store.append_event(agent="guide", type="guide_delegate", parent_event_id=th["event_id"],
                            session_id="resume", payload={"task_card": _CARD})
    ga = store.append_event(agent="guide", type="guide_assign", parent_event_id=de["event_id"],
                            session_id="resume",
                            payload={"task_card_event_id": de["event_id"], "assignee_instance": "merchant#1"})
    create_worktree("merchant#1", repo_root=repo, base_dir=wtb)  # builder 已建 worktree
    store.append_event(agent="merchant#1", type="tool_intent", parent_event_id=ga["event_id"],
                       session_id="resume",
                       payload={"tool": "write", "params": {"path": "login.py"}, "task_id": "t-login-impl"})  # unpaired
    n_before = len(store.query_session())
    store.close()  # 强杀(进程结束)

    # --- 进程2:干净重启,reopen 同一 db,advance 续作 ---
    store2 = SessionStore(db)
    report = advance(store2, llm_client=_StubLLM(),
                     repo_root=repo, worktrees_base=wtb,
                     builder_scripted_actions=_BUILDER_ACTIONS)

    events = store2.query_session()
    types = [e["type"] for e in events]
    # finish_build 续作:补出 review_request → verify_run → review_verdict(pass) → merge
    assert "review_request" in types, f"续作未补出 review_request: {types}"
    assert "verify_run" in types, f"续作未到验证: {types}"
    rv = next(e for e in events if e["type"] == "review_verdict")
    assert rv["payload"]["verdict"] == "pass" and rv["agent"] == "tailor#1"
    assert any(e["type"] == "merge" for e in events), "续作应到 merge 终态"
    # 任务板:完整重建为 verified_pass
    assert report["task_board"]["t-login-impl"]["status"] == "verified_pass"
    # append-only:崩溃前事件未丢,新事件在其后追加
    assert len(events) > n_before


def test_resume_at_quiescence_is_noop(tmp_path):
    """已完成的 session 重启后再 advance 不产生新事件(续作幂等)。"""
    repo = tmp_path / "repo"; repo.mkdir(); _git_init_repo(repo)
    db = tmp_path / "session.db"
    wtb = tmp_path / "worktrees"
    store = SessionStore(db)
    store.append_event(agent="user", type="user_command", payload={"text": "做个登录"}, session_id="resume")
    advance(store, llm_client=_StubLLM(), repo_root=repo, worktrees_base=wtb,
            builder_scripted_actions=_BUILDER_ACTIONS)
    store.close()

    store2 = SessionStore(db)
    n1 = len(store2.query_session())
    advance(store2, llm_client=_StubLLM(), repo_root=repo, worktrees_base=wtb,
            builder_scripted_actions=_BUILDER_ACTIONS)
    assert len(store2.query_session()) == n1, "完成态重启后不应再生事件"
