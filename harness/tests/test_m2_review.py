"""M2.1 reviewer (tailor) 审查步测试(decision 甲)。

tailor 在隔离 context 上出 review_verdict;机器判定权威(INV-5)由代码强制凌驾 LLM:
- verify_run 失败 → reject(跳过 LLM)
- 无通过 verify_run → 即使 LLM 说 pass 也降级 reject
- 审查用的是隔离 context(无 npc_think)

离线确定性(stub LLM)。live tailor 审查(真实 DeepSeek)在文末 skipif-gated。
"""
import json
import os

import pytest

from harness.session.store import SessionStore
from harness.sandbox.review_executor import review_task

_SENTINEL = "SENTINEL_NPC_REASONING_MUST_NOT_LEAK"

_CARD = {
    "task_id": "t-login-impl", "assignee_role": "builder",
    "objective": "实现 login.py", "output_format": "login.py",
    "allowed_tools": ["read", "write", "bash"], "boundaries": ["只用标准库"],
    "verification": [{"type": "machine_verifiable",
                      "command": "python -c \"import login; print('OK')\"",
                      "expected": {"exit_code": 0}}],
}


class _StubTailor:
    """捕获 messages 的 LLM stub;complete 返回预设 JSON。"""
    def __init__(self, resp, *, fail_if_called=False):
        self.resp = resp
        self.fail_if_called = fail_if_called
        self.last_messages = None

    def complete(self, model, messages, **kwargs):
        if self.fail_if_called:
            raise AssertionError("LLM 不应被调用(机器判定权威应已短路)")
        self.last_messages = messages
        return self.resp


def _seed(tmp_path, *, verify_passed=True, with_npc_think=True, with_verify=True):
    """建 session: delegate → assign(merchant) → [npc_think] → tool_done → review_request
    → [verify_run] → assign(tailor#1)。返回 (store, tailor_assign_eid)。

    with_verify=False:不落 verify_run(测"无 verify_run 时不得 pass"的后置约束)。
    """
    store = SessionStore(tmp_path / "session.db")
    sid = "m2-review"
    de = store.append_event(agent="guide", type="guide_delegate", session_id=sid,
                            payload={"task_card": _CARD})
    ga_b = store.append_event(agent="guide", type="guide_assign", parent_event_id=de["event_id"],
                              session_id=sid,
                              payload={"task_card_event_id": de["event_id"], "assignee_instance": "merchant#1"})
    last = ga_b["event_id"]
    if with_npc_think:
        nt = store.append_event(agent="merchant#1", type="npc_think", parent_event_id=last, session_id=sid,
                                payload={"text": _SENTINEL + ": 偷工减料了", "task_id": "t-login-impl"})
        last = nt["event_id"]
    ti = store.append_event(agent="merchant#1", type="tool_intent", parent_event_id=last, session_id=sid,
                            payload={"tool": "write", "params": {"path": "login.py"}, "task_id": "t-login-impl"})
    store.append_event(agent="merchant#1", type="tool_done", parent_event_id=ti["event_id"], session_id=sid,
                       payload={"tool": "write", "status": "ok", "file": "login.py",
                                "hash": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"})
    rr = store.append_event(agent="merchant#1", type="review_request", parent_event_id=ga_b["event_id"], session_id=sid,
                            payload={"task_id": "t-login-impl", "reviewer": "tailor",
                                     "artifact": {"files": ["login.py"], "worktree": "data/worktrees/merchant-1"}})
    if with_verify:
        store.append_event(agent="blaster#1", type="verify_run", parent_event_id=rr["event_id"], session_id=sid,
                           payload={"task_id": "t-login-impl", "command": _CARD["verification"][0]["command"],
                                    "exit_code": 0 if verify_passed else 1, "passed": verify_passed,
                                    "output_summary": "OK" if verify_passed else "FAIL"})
    ga_t = store.append_event(agent="guide", type="guide_assign", parent_event_id=rr["event_id"], session_id=sid,
                              payload={"task_card_event_id": de["event_id"], "assignee_instance": "tailor#1"})
    return store, ga_t["event_id"]


def test_review_machine_fail_is_reject_without_llm(tmp_path):
    """verify_run 失败 → reject,且 LLM 不被调用(机器判定权威短路)。"""
    store, ga_t = _seed(tmp_path, verify_passed=False)
    stub = _StubTailor('{"verdict":"pass","notes":"x"}', fail_if_called=True)
    eids = review_task("tailor#1", _CARD, store, ga_t, llm_client=stub)
    v = store.get_event(eids[0])
    assert v["type"] == "review_verdict" and v["agent"] == "tailor#1"
    assert v["payload"]["reviewer"] == "tailor#1"
    assert v["payload"]["verdict"] == "reject"
    assert v["parent_event_id"] == ga_t


def test_review_machine_pass_tailor_pass(tmp_path):
    """verify_run 通过 + tailor LLM 说 pass → review_verdict pass。"""
    store, ga_t = _seed(tmp_path, verify_passed=True)
    stub = _StubTailor('{"verdict":"pass","notes":"代码符合边界,测试通过"}')
    eids = review_task("tailor#1", _CARD, store, ga_t, llm_client=stub)
    v = store.get_event(eids[0])
    assert v["payload"]["verdict"] == "pass"
    assert v["payload"]["reviewer"] == "tailor#1"


def test_review_pass_requires_passing_verify_run(tmp_path):
    """无 verify_run 时(机器闸未通过),即使 LLM 说 pass 也降级 reject(INV-5 后置硬约束)。"""
    store, ga_t = _seed(tmp_path, with_verify=False)   # 无 verify_run → machine_failed=False 但无 passing
    stub = _StubTailor('{"verdict":"pass","notes":"我觉得行"}')  # LLM 会被调用,返回 pass
    eids = review_task("tailor#1", _CARD, store, ga_t, llm_client=stub)
    v = store.get_event(eids[0])["payload"]
    assert v["verdict"] == "reject", "无通过 verify_run 不得 pass"
    assert "无通过的 verify_run" in v["notes"]


def test_review_uses_isolated_context(tmp_path):
    """tailor 审查用的 messages 不含制作者 npc_think 哨兵(隔离在审查步生效)。"""
    store, ga_t = _seed(tmp_path, verify_passed=True, with_npc_think=True)
    stub = _StubTailor('{"verdict":"pass","notes":"ok"}')
    review_task("tailor#1", _CARD, store, ga_t, llm_client=stub)
    joined = "\n".join(m["content"] for m in stub.last_messages)
    assert _SENTINEL not in joined, "审查步传给 LLM 的 context 泄露了 npc_think 哨兵"
    assert "login.py" in joined, "审查 context 应含事实"


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("DEEPSEEK_API_KEY"),
                    reason="DEEPSEEK_API_KEY not set; skipping live tailor review")
def test_review_live_deepseek(tmp_path, monkeypatch):
    """live: 真实 DeepSeek tailor 在隔离 context 上审查通过的任务,出合法 verdict。"""
    monkeypatch.setenv("TERRA_LLM_MODE", "real")
    store, ga_t = _seed(tmp_path, verify_passed=True)
    eids = review_task("tailor#1", _CARD, store, ga_t, llm_client=None)
    v = store.get_event(eids[0])
    assert v["type"] == "review_verdict" and v["agent"] == "tailor#1"
    assert v["payload"]["verdict"] in ("pass", "reject")
