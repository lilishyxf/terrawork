"""M2.2b 退回重做 + M2.7-4 双审查聚合(ADR-019)。

任一 reviewer reject → 重派 builder 返工(复用 worktree + 注入 notes),≤ max_rework 轮;
超限 → hitl。双审查:tailor(代码审查)+ appsec(安全审查)各审一次,全 pass 才 merge。
离线确定性:guide 单卡 stub + builder scripted + 有状态 reviewer stub(分 tailor/appsec 控制序列)。
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
    """guide 恒返单卡;两位 reviewer 各按预设 verdict 序列(最后一个用于其后所有调用)。
    appsec 缺省恒 pass(只验安全那条线时不干扰)。appsec.md 提示词含'应用安全',据此先于'裁缝'分派。"""
    def __init__(self, tailor_verdicts, appsec_verdicts=None):
        self._t = list(tailor_verdicts)
        self._a = list(appsec_verdicts) if appsec_verdicts else None
        self.tcalls = 0
        self.acalls = 0

    def complete(self, model, messages, **kwargs):
        system = messages[0]["content"] if messages else ""
        if "应用安全" in system:  # appsec 先判(其 prompt 也提到"裁缝")
            if self._a is None:
                return json.dumps({"verdict": "pass", "notes": "安全通过"}, ensure_ascii=False)
            i = min(self.acalls, len(self._a) - 1)
            self.acalls += 1
            return json.dumps({"verdict": self._a[i], "notes": f"appsec第{self.acalls}次:{self._a[i]}"},
                              ensure_ascii=False)
        if "裁缝" in system or "Tailor" in system:
            i = min(self.tcalls, len(self._t) - 1)
            self.tcalls += 1
            return json.dumps({"verdict": self._t[i], "notes": f"tailor第{self.tcalls}次:{self._t[i]}"},
                              ensure_ascii=False)
        return _GUIDE_JSON


def _run(tmp_path, tailor_verdicts, appsec_verdicts=None, max_rework=2):
    repo = tmp_path / "repo"; repo.mkdir(); _git_init_repo(repo)
    store = SessionStore(tmp_path / "session.db")
    store.append_event(agent="user", type="user_command",
                       payload={"text": "做个登录"}, session_id="rework")
    report = advance(store, llm_client=_StubLLM(tailor_verdicts, appsec_verdicts),
                     repo_root=repo, worktrees_base=tmp_path / "worktrees",
                     builder_scripted_actions=_BUILDER_ACTIONS, max_rework=max_rework)
    return store, report


def _verdicts_by(events, reviewer):
    return [e["payload"]["verdict"] for e in events
            if e["type"] == "review_verdict" and e["payload"]["reviewer"] == reviewer]


def _builder_assigns(events):
    return [e for e in events if e["type"] == "guide_assign"
            and e["payload"]["assignee_instance"] == "merchant#1"]


def test_rework_then_pass(tmp_path):
    """tailor 首轮 reject → 返工 → 次轮双审查全 pass → merge(appsec 恒 pass)。"""
    store, report = _run(tmp_path, tailor_verdicts=["reject", "pass"])
    events = store.query_session()
    assert _verdicts_by(events, "tailor#1") == ["reject", "pass"]
    assert _verdicts_by(events, "appsec#1") == ["pass", "pass"]  # 两轮都安全通过
    assert len(_builder_assigns(events)) == 2, "应 2 次 builder 派发(初始+返工)"
    assert sum(1 for e in events if e["type"] == "verify_run") == 2  # 两轮各一次验证
    assert any(e["type"] == "merge" for e in events), "次轮全 pass 后应 merge"
    assert report["task_board"]["t-login-impl"]["status"] == "verified_pass"


def test_appsec_reject_triggers_rework(tmp_path):
    """双审查聚合:tailor 全 pass 但 appsec 首轮 reject → 仍返工;次轮全 pass → merge。"""
    store, report = _run(tmp_path, tailor_verdicts=["pass"], appsec_verdicts=["reject", "pass"])
    events = store.query_session()
    assert _verdicts_by(events, "appsec#1") == ["reject", "pass"]
    assert len(_builder_assigns(events)) == 2, "appsec reject 也应触发返工(任一 reject→返工)"
    assert any(e["type"] == "merge" for e in events)
    assert report["task_board"]["t-login-impl"]["status"] == "verified_pass"


def test_rework_exhausted_escalates_to_hitl(tmp_path):
    """持续 reject:返工 max_rework 轮后超限 → hitl 兜底,不无限循环。"""
    store, report = _run(tmp_path, tailor_verdicts=["reject"], max_rework=2)
    events = store.query_session()
    # max_rework=2 → 3 轮(初始+2 返工),每轮 tailor 各 reject 一次 → 3 次 tailor reject
    assert _verdicts_by(events, "tailor#1") == ["reject", "reject", "reject"]
    assert len(_builder_assigns(events)) == 3, "初始 + 2 返工 = 3 次 builder 派发"
    assert any(e["type"] == "hitl_request" for e in events), "超限应 hitl 兜底"
    assert not any(e["type"] == "merge" for e in events), "全 reject 不应 merge"
    assert report["task_board"]["t-login-impl"]["status"] == "rejected"


def test_rework_builds_in_reused_worktree(tmp_path):
    """返工不因 worktree 已存在而崩(reuse 生效);单卡 → per-instance worktree(ADR-016,merchant-1)。"""
    store, _ = _run(tmp_path, tailor_verdicts=["reject", "pass"])
    wt = tmp_path / "worktrees" / "merchant-1"
    assert wt.is_dir() and (wt / ".git").exists()
    # 只有 builder 自己的 worktree(初始+返工复用,未另建);验证 worktree 用后即销
    others = [p for p in (tmp_path / "worktrees").iterdir() if p.is_dir()]
    assert others == [wt], f"返工应复用单一 builder worktree,实际 {others}"
