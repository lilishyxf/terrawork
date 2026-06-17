"""M1.4-5a orchestrator wiring (offline, deterministic).

证编排接线正确:user_command -> guide 分解 -> 派 builder 执行 -> 派 verifier 验证 -> verdict。
guide 用单卡 stub(决策 B1),builder 用 execute_npc(scripted_actions),verify/verdict/wake 确定性。
0 LLM 成本。live 全链(真实 DeepSeek)留 5b。
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from harness.session.store import SessionStore
from harness.orchestrator import advance
from harness.wake import wake


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


class _StubLLM:
    """LLM stub:按 system prompt 分派——guide 分解 vs tailor 审查(M2.2a 编排两处都调 LLM)。"""
    def complete(self, model, messages, **kwargs):
        system = messages[0]["content"] if messages else ""
        if "裁缝" in system or "Tailor" in system:
            return '{"verdict": "pass", "notes": "代码符合边界,验证通过"}'
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
        store, llm_client=_StubLLM(),
        repo_root=repo, worktrees_base=tmp_path / "worktrees",
        builder_scripted_actions=builder_actions,
    )

    # 全链事件序列(M2.2a 单卡流:verify -> tailor review -> merge)
    types = [e["type"] for e in store.query_session()]
    assert types == [
        "user_command", "guide_think", "guide_delegate", "guide_assign",
        "tool_intent", "tool_done", "review_request",
        "guide_assign", "verify_run",
        "guide_assign", "review_verdict", "merge",
    ], f"事件链不符: {types}"

    events = store.query_session()
    # verifier 是 blaster#1、在 builder worktree 跑、verify_run passed
    vr = next(e for e in events if e["type"] == "verify_run")
    assert vr["agent"] == "blaster#1"
    assert vr["payload"]["command"] == _LOGIN_CARD["verification"][0]["command"]  # 逐字
    assert vr["payload"]["passed"] is True
    # tailor 出 review_verdict = pass(decision 甲)
    rv = next(e for e in events if e["type"] == "review_verdict")
    assert rv["agent"] == "tailor#1" and rv["payload"]["verdict"] == "pass"
    # Guide 仲裁 merge 终态
    mg = next(e for e in events if e["type"] == "merge")
    assert mg["agent"] == "guide" and mg["payload"]["result"] == "success"
    assert mg["parent_event_id"] == rv["event_id"]
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
    kw = dict(llm_client=_StubLLM(), repo_root=repo,
              worktrees_base=tmp_path / "worktrees", builder_scripted_actions=actions)
    advance(store, **kw)
    n1 = len(store.query_session())
    advance(store, **kw)  # 再驱动一次
    n2 = len(store.query_session())
    assert n2 == n1, f"静止后不应再生事件: {n1} -> {n2}"


# ---- M1.4-5b: live 全链(真实 DeepSeek) + 干净机器 wake 演示 ----

# 决策 (i):收窄为原子任务,强 steer 单卡(M1 单卡流);"模糊指令→多卡 test-first 自动拆解"留 M2。
_LIVE_USER_COMMAND = (
    "实现一个 login.py(单文件,仅用 Python 标准库):暴露 login(username, password) -> dict,"
    "返回 {'ok': bool}。这是一个原子任务,只产**一张**任务卡(assignee_role: builder);"
    "其 verification 用一条 `python -c \"...\"` 命令断言行为(expected.exit_code=0),"
    "不要拆成多张卡,不要写测试文件。"
)


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY not set; skipping live full-chain test",
)
def test_orchestrator_full_loop_live_deepseek(tmp_path, monkeypatch):
    """M1.4-5b:真实 DeepSeek 跑 guide 分解 + builder 执行,全链到 verdict;再干净机器 wake。

    单卡流(决策 B/(i)):若 guide 产多 builder 卡,orchestrator 抛 NotImplementedError(M1 边界)。
    LLM 轨迹不可预测,只校验结构:全链到 review_verdict、verify_run 真跑、wake 重建一致。
    """
    monkeypatch.setenv("TERRA_LLM_MODE", "real")

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init_repo(repo)
    db_path = tmp_path / "session.db"
    worktrees = tmp_path / "worktrees"

    store = SessionStore(db_path)
    store.append_event(agent="user", type="user_command",
                       payload={"text": _LIVE_USER_COMMAND}, session_id="orch-live")

    report = advance(store, llm_client=None,  # None + TERRA_LLM_MODE=real → 真实 DeepSeek
                     repo_root=repo, worktrees_base=worktrees, max_steps=60)

    events = store.query_session()
    types = [e["type"] for e in events]

    # 全链:分解 → 验证 → tailor 审查 → 仲裁终态
    assert "guide_delegate" in types, f"guide 未分解: {types}"
    assert "verify_run" in types, f"未到验证: {types}"
    assert "review_verdict" in types, f"未到审查: {types}"

    vr = next(e for e in events if e["type"] == "verify_run")
    rv = next(e for e in events if e["type"] == "review_verdict")
    assert rv["agent"] == "tailor#1", "review_verdict 应由 tailor 出(decision 甲)"

    # 机器判定权威:verify 失败 → 必 reject(tailor 不可推翻机器)
    if not vr["payload"]["passed"]:
        assert rv["payload"]["verdict"] == "reject", "verify 失败但 verdict 非 reject"

    # 仲裁终态:pass → merge;reject → hitl_request(LLM 审查判断不可预测,两结局都接受)
    if rv["payload"]["verdict"] == "pass":
        assert "merge" in types, "verdict pass 但无 merge 终态"
        expected_status = "verified_pass"
    else:
        assert "hitl_request" in types, "verdict reject 但无 hitl 兜底"
        expected_status = "rejected"

    # 干净机器 wake 演示:换全新 SessionStore 打开同一 db,纯日志重建状态、不报错
    store.close()
    fresh = SessionStore(db_path)
    fresh_report = wake(fresh, worktrees_base=worktrees)
    fresh.close()
    assert fresh_report["task_board"], "wake 未重建任务板"
    tid = next(iter(fresh_report["task_board"]))
    assert fresh_report["task_board"][tid]["status"] == expected_status, \
        f"干净机器 wake 后状态不符: {fresh_report['task_board']} (期望 {expected_status})"
    assert fresh_report["unpaired_intents"] == [], "全链跑完不应有 unpaired intent"
