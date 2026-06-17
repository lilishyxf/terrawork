"""M2.2b 退回重做循环测试。

reject → 重派 builder 返工(复用 worktree + 注入 reject notes),≤ max_rework 次;超限 → hitl。
离线确定性:guide 单卡 stub + builder scripted + 有状态 tailor stub(控制 reject/pass 序列)。
"""
import json
import subprocess
from pathlib import Path

from harness.session.store import SessionStore
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


def _git_init_repo(repo: Path):
    for c in (["git", "init", "-b", "main"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"], ["git", "commit", "--allow-empty", "-m", "init"]):
        subprocess.run(c, cwd=repo, check=True, capture_output=True)


class _StubLLM:
    """guide 恒返单卡;tailor 按预设 verdict 序列逐次返回(最后一个用于其后所有调用)。"""
    def __init__(self, tailor_verdicts):
        self._tv = list(tailor_verdicts)
        self.tailor_calls = 0

    def complete(self, model, messages, **kwargs):
        system = messages[0]["content"] if messages else ""
        if "裁缝" in system or "Tailor" in system:
            i = min(self.tailor_calls, len(self._tv) - 1)
            self.tailor_calls += 1
            verdict = self._tv[i]
            return json.dumps({"verdict": verdict, "notes": f"审查第{self.tailor_calls}次:{verdict}"},
                              ensure_ascii=False)
        return _GUIDE_JSON


def _run(tmp_path, tailor_verdicts, max_rework=2):
    repo = tmp_path / "repo"; repo.mkdir(); _git_init_repo(repo)
    store = SessionStore(tmp_path / "session.db")
    store.append_event(agent="user", type="user_command",
                       payload={"text": "做个登录"}, session_id="rework")
    report = advance(store, llm_client=_StubLLM(tailor_verdicts),
                     repo_root=repo, worktrees_base=tmp_path / "worktrees",
                     builder_scripted_actions=_BUILDER_ACTIONS, max_rework=max_rework)
    return store, report


def test_rework_then_pass(tmp_path):
    """首轮 reject → 返工(复用 worktree)→ 次轮 pass → merge。"""
    store, report = _run(tmp_path, tailor_verdicts=["reject", "pass"])
    events = store.query_session()
    verdicts = [e["payload"]["verdict"] for e in events if e["type"] == "review_verdict"]
    assert verdicts == ["reject", "pass"], f"verdict 序列应 reject→pass,实际 {verdicts}"
    # 返工:reject 后有第二个 builder guide_assign
    builder_assigns = [e for e in events if e["type"] == "guide_assign"
                       and e["payload"]["assignee_instance"] == "merchant#1"]
    assert len(builder_assigns) == 2, f"应有 2 次 builder 派发(初始+返工),实际 {len(builder_assigns)}"
    # 两轮 verify、最终 merge
    assert sum(1 for e in events if e["type"] == "verify_run") == 2
    assert any(e["type"] == "merge" for e in events), "次轮 pass 后应 merge"
    assert report["task_board"]["t-login-impl"]["status"] == "verified_pass"


def test_rework_exhausted_escalates_to_hitl(tmp_path):
    """持续 reject:返工 max_rework 次后超限 → hitl 兜底,不无限循环。"""
    store, report = _run(tmp_path, tailor_verdicts=["reject"], max_rework=2)
    events = store.query_session()
    rejects = [e for e in events if e["type"] == "review_verdict" and e["payload"]["verdict"] == "reject"]
    assert len(rejects) == 3, f"max_rework=2 → 3 次 reject(初始+2 返工),实际 {len(rejects)}"
    assert any(e["type"] == "hitl_request" for e in events), "超限应 hitl 兜底"
    assert not any(e["type"] == "merge" for e in events), "全 reject 不应 merge"
    assert report["task_board"]["t-login-impl"]["status"] == "rejected"


def test_rework_builds_in_reused_worktree(tmp_path):
    """返工不因 worktree 已存在而崩(reuse_worktree 生效);单 worktree 始终是 merchant-1。"""
    store, _ = _run(tmp_path, tailor_verdicts=["reject", "pass"])
    wt = tmp_path / "worktrees" / "merchant-1"
    assert wt.is_dir() and (wt / ".git").exists()
    # 只有一个 builder worktree 目录(返工复用,未另建)
    others = [p for p in (tmp_path / "worktrees").iterdir() if p.is_dir()]
    assert others == [wt], f"返工应复用单一 worktree,实际 {others}"
