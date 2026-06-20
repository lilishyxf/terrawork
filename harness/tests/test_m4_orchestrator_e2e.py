"""M4-3 编排器消费写半边事件 e2e(ADR-022)。

INV-5 hitl_response.answer → 重派 builder(注入 text,绕过 max_rework)→ 收敛 merge。
INV-6 hitl_response.reject → 任务终态放弃,无新派发、无 merge。
INV-7 追加 user_command → 触发新分解(新任务进板),不影响既有任务。
确定性:stub guide 单卡 + scripted builder + 可控审查(pass_review 在 advance 之间翻)。
"""
import json
import subprocess
from pathlib import Path

from harness.session.store import SessionStore
from harness.orchestrator import advance


def _card(tid, mod):
    return {"task_id": tid, "assignee_role": "builder", "objective": f"实现 {mod}",
            "output_format": f"{mod}.py", "allowed_tools": ["read", "write", "bash"],
            "boundaries": ["只用标准库"],
            "verification": [{"type": "machine_verifiable",
                              "command": f"python -c \"import {mod}\"", "expected": {"exit_code": 0}}]}


def _actions(mod, tid):
    return [{"tool": "write", "params": {"path": f"{mod}.py", "content": f"# {mod}\nOK = 1\n"}, "task_id": tid}]


_SCRIPTED = {"t-login": _actions("login", "t-login"), "t-logout": _actions("logout", "t-logout")}


class _Stub:
    """guide 按用户文本返卡;审查由 pass_review 控(advance 之间翻)。"""
    def __init__(self):
        self.pass_review = False

    def complete(self, model, messages, **kw):
        sys_ = messages[0]["content"] if messages else ""
        usr = messages[-1]["content"] if len(messages) > 1 else ""
        if "应用安全" in sys_ or "裁缝" in sys_ or "Tailor" in sys_:
            v = "pass" if self.pass_review else "reject"
            return json.dumps({"verdict": v, "notes": "改一下" if v == "reject" else "ok"}, ensure_ascii=False)
        card = _card("t-logout", "logout") if "登出" in usr else _card("t-login", "login")
        return json.dumps({"thinking": "x", "tasks": [card]}, ensure_ascii=False)


def _repo(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    for c in (["git", "init", "-b", "main"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"], ["git", "commit", "--allow-empty", "-m", "init"]):
        subprocess.run(c, cwd=repo, check=True, capture_output=True)
    return repo


def _run(store, stub, repo, wt):
    advance(store, llm_client=stub, repo_root=repo, worktrees_base=wt,
            builder_scripted_actions=_SCRIPTED, max_rework=2)


def _bld_assigns(events, tid_delegate_eid):
    return [e for e in events if e["type"] == "guide_assign"
            and e["payload"].get("task_card_event_id") == tid_delegate_eid
            and e["payload"]["assignee_instance"].startswith("merchant")]


def test_hitl_answer_reworks_bypassing_max_rework(tmp_path):
    """INV-5:返工耗尽→hitl;answer→重派 builder(绕过 max_rework)→ 审查转 pass → merge。"""
    repo = _repo(tmp_path); wt = tmp_path / "wt"
    store = SessionStore(tmp_path / "s.db", session_id="m4")
    store.append_event(agent="user", type="user_command", payload={"text": "做个登录"}, session_id="m4")
    stub = _Stub()  # pass_review=False → 持续 reject

    _run(store, stub, repo, wt)
    ev1 = store.query_session()
    hitl = next((e for e in ev1 if e["type"] == "hitl_request"), None)
    assert hitl is not None, "返工耗尽应上抬 hitl"
    assert not any(e["type"] == "merge" for e in ev1), "未通过不应 merge"
    delegate = next(e for e in ev1 if e["type"] == "guide_delegate")
    assigns_before = len(_bld_assigns(ev1, delegate["event_id"]))

    # 人在 HITL 给整改指引(answer)
    resp = store.append_event(agent="user", type="hitl_response",
                              payload={"decision": "answer", "text": "密码用 hashlib"},
                              parent_event_id=hitl["event_id"], session_id="m4")
    stub.pass_review = True  # 这轮审查通过
    _run(store, stub, repo, wt)
    ev2 = store.query_session()

    # 答复之后有新的 builder 重派(hitl_rework 绕过 max_rework)
    rework_after = [a for a in _bld_assigns(ev2, delegate["event_id"]) if a["event_id"] > resp["event_id"]]
    assert rework_after, "answer 应触发 builder 重派(绕过 max_rework)"
    assert len(_bld_assigns(ev2, delegate["event_id"])) > assigns_before
    # 收敛:merge + 任务板 verified_pass
    assert any(e["type"] == "merge" and e["payload"]["result"] == "success" for e in ev2)
    from harness.view.projection import project
    assert project(ev2)["task_board"]["t-login"]["status"] == "merged"


def test_hitl_reject_abandons_task(tmp_path):
    """INV-6:reject → 无新派发、无 merge,任务终态。"""
    repo = _repo(tmp_path); wt = tmp_path / "wt"
    store = SessionStore(tmp_path / "s.db", session_id="m4")
    store.append_event(agent="user", type="user_command", payload={"text": "做个登录"}, session_id="m4")
    stub = _Stub()
    _run(store, stub, repo, wt)
    ev1 = store.query_session()
    hitl = next(e for e in ev1 if e["type"] == "hitl_request")
    delegate = next(e for e in ev1 if e["type"] == "guide_delegate")
    n_before = len(_bld_assigns(ev1, delegate["event_id"]))

    resp = store.append_event(agent="user", type="hitl_response", payload={"decision": "reject"},
                              parent_event_id=hitl["event_id"], session_id="m4")
    _run(store, stub, repo, wt)  # reject → stage6 跳过 → 静止
    ev2 = store.query_session()
    assert len(_bld_assigns(ev2, delegate["event_id"])) == n_before, "reject 不应再派 builder"
    assert not any(e["type"] == "merge" for e in ev2)
    assert all(e["event_id"] <= resp["event_id"] or e["type"] in ("hitl_response",)
               for e in ev2 if e["type"] == "guide_assign"), "reject 后无新编排动作"


def test_followup_command_triggers_new_decompose(tmp_path):
    """INV-7:追加第二条 user_command → 新分解(新任务),不影响已合并的第一个。"""
    repo = _repo(tmp_path); wt = tmp_path / "wt"
    store = SessionStore(tmp_path / "s.db", session_id="m4")
    stub = _Stub(); stub.pass_review = True  # 全程通过
    c1 = store.append_event(agent="user", type="user_command", payload={"text": "做个登录"}, session_id="m4")
    _run(store, stub, repo, wt)
    ev1 = store.query_session()
    assert any(e["type"] == "merge" and e["payload"]["task_id"] == "t-login" for e in ev1)

    # 追加第二条指令
    c2 = store.append_event(agent="user", type="user_command", payload={"text": "再加个登出"}, session_id="m4")
    _run(store, stub, repo, wt)
    ev2 = store.query_session()

    # 两条 command 各被分解(各有 guide_think 以其为 parent)
    for c in (c1, c2):
        assert any(e["type"] == "guide_think" and e.get("parent_event_id") == c["event_id"] for e in ev2), \
            f"command #{c['event_id']} 应被分解"
    # 两个任务都进板且都 merged
    from harness.view.projection import project
    tb = project(ev2)["task_board"]
    assert tb["t-login"]["status"] == "merged" and tb["t-logout"]["status"] == "merged"
