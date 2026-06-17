"""M1.4-5a orchestrator wiring (offline, deterministic).

证编排接线正确:user_command -> guide 分解 -> 派 builder 执行 -> 派 verifier 验证 -> verdict。
guide 用单卡 stub(决策 B1),builder 用 execute_npc(scripted_actions),verify/verdict/wake 确定性。
0 LLM 成本。live 全链(真实 DeepSeek)留 5b。
"""
import json
import subprocess
from pathlib import Path

from harness.session.store import SessionStore
from harness.orchestrator import advance


def _git_init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, check=True, capture_output=True)


# 单卡 guide stub(决策 B1):做个登录 -> 1 个 builder 卡,自带可跑 verification command
_LOGIN_CARD = {
    "task_id": "t-login-impl",
    "assignee_role": "builder",
    "objective": "实现 login.py,暴露 login(username, password) -> dict",
    "output_format": "单文件 login.py 在 worktree 根",
    "allowed_tools": ["read", "write", "bash"],
    "boundaries": ["只用 Python 标准库", "密码永不明文存储"],
    "verification": [{
        "type": "machine_verifiable",
        "command": "python -c \"import login; print('OK')\"",
        "expected": {"exit_code": 0, "stdout_contains": "OK"},
    }],
}
_GUIDE_STUB_JSON = json.dumps(
    {"thinking": "单卡:建 login.py 并以 python import 自验。", "tasks": [_LOGIN_CARD]},
    ensure_ascii=False,
)


class _StubGuide:
    """最小 LLM stub:guide_step 只调 .complete(model, messages)。"""
    def complete(self, model, messages, **kwargs):
        return _GUIDE_STUB_JSON


def test_orchestrator_full_loop_offline(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init_repo(repo)
    store = SessionStore(tmp_path / "session.db")
    store.append_event(agent="user", type="user_command",
                       payload={"text": "做个登录"}, session_id="orch-e2e")

    builder_actions = {"t-login-impl": [
        {"tool": "write",
         "params": {"path": "login.py", "content": "def login(u, p):\n    return {'ok': True}\n"},
         "task_id": "t-login-impl"}
    ]}

    report = advance(
        store, llm_client=_StubGuide(),
        repo_root=repo, worktrees_base=tmp_path / "worktrees",
        builder_scripted_actions=builder_actions,
    )

    # 全链事件序列(单卡流)
    types = [e["type"] for e in store.query_session()]
    assert types == [
        "user_command", "guide_think", "guide_delegate", "guide_assign",
        "tool_intent", "tool_done", "review_request",
        "guide_assign", "verify_run", "review_verdict",
    ], f"事件链不符: {types}"

    events = store.query_session()
    # verifier 是 blaster#1、在 builder worktree 跑、verify_run passed
    vr = next(e for e in events if e["type"] == "verify_run")
    assert vr["agent"] == "blaster#1"
    assert vr["payload"]["command"] == _LOGIN_CARD["verification"][0]["command"]  # 逐字
    assert vr["payload"]["passed"] is True
    # Guide verdict = pass(机器判定权威)
    rv = next(e for e in events if e["type"] == "review_verdict")
    assert rv["agent"] == "guide" and rv["payload"]["verdict"] == "pass"
    # wake 最终任务板
    assert report["task_board"]["t-login-impl"]["status"] == "verified_pass"
    assert report["unpaired_intents"] == []


def test_orchestrator_idempotent_at_quiescence(tmp_path):
    """到静止后再 advance 一次不应产生新事件(收敛稳定)。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init_repo(repo)
    store = SessionStore(tmp_path / "session.db")
    store.append_event(agent="user", type="user_command",
                       payload={"text": "做个登录"}, session_id="orch-e2e")
    actions = {"t-login-impl": [
        {"tool": "write", "params": {"path": "login.py", "content": "def login(u, p):\n    return {'ok': True}\n"},
         "task_id": "t-login-impl"}
    ]}
    kw = dict(llm_client=_StubGuide(), repo_root=repo,
              worktrees_base=tmp_path / "worktrees", builder_scripted_actions=actions)
    advance(store, **kw)
    n1 = len(store.query_session())
    advance(store, **kw)  # 再驱动一次
    n2 = len(store.query_session())
    assert n2 == n1, f"静止后不应再生事件: {n1} -> {n2}"
