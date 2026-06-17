"""M1.4-3 Guide verdict layer: decide_verdict() + m14b fail-path contract.

- m14b self-consistency (data-scope INV on reference_output)
- decide_verdict runtime e2e: pass → review_verdict(pass);
  fail → review_verdict(reject) + hitl_request (option b)
确定性、离线(不调 LLM)。
"""
import json
from pathlib import Path

from harness.session.schema import validate_event
from harness.session.store import SessionStore
from harness.guide.verdict import decide_verdict

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name, encoding="utf-8") as f:
        return json.load(f)


# ---- m14b data-scope INV ----

def _task_card(trigger, prior):
    tc_eid = trigger["payload"]["task_card_event_id"]
    return next(
        e["payload"]["task_card"] for e in prior
        if e["type"] == "guide_delegate" and e["event_id"] == tc_eid
    )


def check_inv_1_verify_run_failed(events):
    runs = [e for e in events if e["type"] == "verify_run"]
    assert len(runs) == 1, f"INV-1: verify_run 应 1 个,实际 {len(runs)}"
    assert runs[0]["payload"]["passed"] is False, "INV-1: fail 路径前提 passed==False 不成立"


def check_inv_2_verdict_well_formed(events, trigger, prior):
    runs = [e for e in events if e["type"] == "verify_run"]
    verdicts = [e for e in events if e["type"] == "review_verdict"]
    assert len(verdicts) == 1, f"INV-2: review_verdict 应 1 个,实际 {len(verdicts)}"
    v = verdicts[0]
    assert v["agent"] == "guide", f"INV-2: verdict agent={v['agent']!r} 应为 'guide'"
    assert v["parent_event_id"] == runs[0]["event_id"], \
        f"INV-2: verdict parent={v['parent_event_id']} 应链回 verify_run#{runs[0]['event_id']}"
    assert v["payload"]["verdict"] in ("pass", "reject"), \
        f"INV-2: verdict={v['payload']['verdict']!r} 应 ∈ {{pass, reject}}"
    assert v["payload"]["task_id"] == _task_card(trigger, prior)["task_id"]


def check_inv_3_machine_judgment_authority_reject(events):
    runs = [e for e in events if e["type"] == "verify_run"]
    verdicts = [e for e in events if e["type"] == "review_verdict"]
    if runs[0]["payload"]["passed"] is False:
        assert verdicts[0]["payload"]["verdict"] != "pass", \
            "INV-3: passed=False 时 verdict 不得为 'pass'(机器判定权威)"


def check_inv_4_hitl_fallback(events, trigger, prior):
    verdicts = [e for e in events if e["type"] == "review_verdict"]
    hitls = [e for e in events if e["type"] == "hitl_request"]
    assert len(hitls) == 1, f"INV-4: hitl_request 应 1 个,实际 {len(hitls)}"
    h = hitls[0]
    assert h["agent"] == "guide", f"INV-4: hitl agent={h['agent']!r} 应为 'guide'"
    assert h["parent_event_id"] == verdicts[0]["event_id"], \
        f"INV-4: hitl parent={h['parent_event_id']} 应链回 review_verdict#{verdicts[0]['event_id']}"
    assert h["payload"]["task_id"] == _task_card(trigger, prior)["task_id"]
    assert h["payload"].get("reason"), "INV-4: hitl reason 非空"
    assert h["payload"].get("question"), "INV-4: hitl question 非空"


def check_inv_5_all_schemas_pass(events):
    for e in events:
        validate_event(e)


def check_inv_6_parent_chain_strictly_backward(events, trigger):
    valid_ids = {trigger["event_id"]} | {e["event_id"] for e in events}
    for e in events:
        pid = e.get("parent_event_id")
        assert isinstance(pid, int) and pid >= 1, f"INV-6: event#{e['event_id']} parent 非正整数"
        assert pid in valid_ids, f"INV-6: event#{e['event_id']} parent={pid} 未链回先前事件"
        assert pid < e["event_id"], f"INV-6: event#{e['event_id']} parent={pid} 必须指向更早事件"


def test_m14b_fail_fixture_self_consistent():
    """M1.4-3 self-consistency: m14b reference_output 通过 fail-path data INV。"""
    fx = load_fixture("m14b_verify_fail.json")
    events = fx["reference_output"]
    trigger = fx["trigger"]
    prior = fx["prior_context"]

    data_invs = [inv for inv in fx["invariants"] if inv["scope"] == "data"]
    assert len(data_invs) == 6, f"m14b 期望 6 条 data-scope INV,实际 {len(data_invs)}"

    check_inv_1_verify_run_failed(events)
    check_inv_2_verdict_well_formed(events, trigger, prior)
    check_inv_3_machine_judgment_authority_reject(events)
    check_inv_4_hitl_fallback(events, trigger, prior)
    check_inv_5_all_schemas_pass(events)
    check_inv_6_parent_chain_strictly_backward(events, trigger)


# ---- decide_verdict runtime e2e ----

def _seed_session_with_verify_run(tmp_path, passed: bool):
    """建 session: guide_delegate → guide_assign(blaster#1) → verify_run(passed),返回 (store, verify_run_eid)。"""
    store = SessionStore(tmp_path / "session.db")
    tc = {
        "task_id": "t-login-impl", "assignee_role": "builder",
        "objective": "实现 login.py", "output_format": "login.py",
        "allowed_tools": ["read", "write", "bash"], "boundaries": ["只用标准库"],
        "verification": [{"type": "machine_verifiable", "command": "python -c \"print('OK')\"",
                          "expected": {"exit_code": 0}}],
    }
    de = store.append_event(agent="guide", type="guide_delegate",
                            payload={"task_card": tc}, session_id="verdict-e2e")
    ga = store.append_event(agent="guide", type="guide_assign",
                            payload={"task_card_event_id": de["event_id"], "assignee_instance": "blaster#1"},
                            parent_event_id=de["event_id"], session_id="verdict-e2e")
    vr = store.append_event(agent="blaster#1", type="verify_run",
                            parent_event_id=ga["event_id"], session_id="verdict-e2e",
                            payload={"task_id": "t-login-impl", "command": "python -c \"print('OK')\"",
                                     "exit_code": 0 if passed else 1, "passed": passed,
                                     "output_summary": "OK" if passed else "FAIL"})
    return store, vr["event_id"]


def test_decide_verdict_pass_runtime(tmp_path):
    store, vr_eid = _seed_session_with_verify_run(tmp_path, passed=True)
    eids = decide_verdict(store, vr_eid)
    assert len(eids) == 1
    v = store.get_event(eids[0])
    assert v["type"] == "review_verdict"
    assert v["agent"] == "guide"
    assert v["payload"]["verdict"] == "pass"     # 机器判定权威: passed=True → pass
    assert v["parent_event_id"] == vr_eid
    assert v["payload"]["task_id"] == "t-login-impl"


def test_decide_verdict_fail_runtime(tmp_path):
    store, vr_eid = _seed_session_with_verify_run(tmp_path, passed=False)
    eids = decide_verdict(store, vr_eid)
    assert len(eids) == 2
    v = store.get_event(eids[0])
    h = store.get_event(eids[1])
    assert v["type"] == "review_verdict" and v["payload"]["verdict"] == "reject"  # passed=False → reject (非 pass)
    assert v["parent_event_id"] == vr_eid
    assert h["type"] == "hitl_request" and h["agent"] == "guide"
    assert h["parent_event_id"] == v["event_id"]                                  # hitl 链回 reject
    assert h["payload"]["task_id"] == "t-login-impl"
    assert h["payload"]["reason"] and h["payload"]["question"]
