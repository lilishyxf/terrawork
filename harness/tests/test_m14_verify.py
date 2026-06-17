"""M1.4 verify + verdict contract tests.

M1.4-1: data-scope INV-1~7 on reference_output (self-consistency).
M1.4-2/3: runtime INV-8 (verifier in builder worktree) — deferred to executor wiring.
"""
import json
from pathlib import Path

from harness.session.schema import validate_event

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name, encoding="utf-8") as f:
        return json.load(f)


# ---- INV-1 ~ INV-7 ----

def check_inv_1_verify_run_well_formed(events, trigger):
    runs = [e for e in events if e["type"] == "verify_run"]
    assert len(runs) == 1, f"INV-1: verify_run 应恰好 1 个,实际 {len(runs)}"
    r = runs[0]
    assert r["agent"] == trigger["payload"]["assignee_instance"], \
        f"INV-1: verify_run agent={r['agent']!r} 应为 {trigger['payload']['assignee_instance']!r}"
    assert r["parent_event_id"] == trigger["event_id"], \
        f"INV-1: verify_run parent={r['parent_event_id']} 应链回 trigger#{trigger['event_id']}"


def check_inv_2_verifier_runs_schema_command_verbatim(events, trigger, prior):
    runs = [e for e in events if e["type"] == "verify_run"]
    r = runs[0]
    tc_eid = trigger["payload"]["task_card_event_id"]
    task_card = next(
        e["payload"]["task_card"] for e in prior
        if e["type"] == "guide_delegate" and e["event_id"] == tc_eid
    )
    schema_cmd = task_card["verification"][0]["command"]
    assert r["payload"]["command"] == schema_cmd, (
        "INV-2: verify_run.command 必须与 task_card.verification[0].command 逐字一致 "
        "(verifier 不自创、不改写)"
    )
    assert r["payload"]["task_id"] == task_card["task_id"], \
        f"INV-2: verify_run.task_id={r['payload']['task_id']!r} 应为 {task_card['task_id']!r}"


def check_inv_3_verify_run_required_fields(events):
    runs = [e for e in events if e["type"] == "verify_run"]
    r = runs[0]
    p = r["payload"]
    for f in ("task_id", "command", "exit_code", "passed"):
        assert f in p, f"INV-3: verify_run.payload 缺 required 字段 {f!r}"
    assert isinstance(p["exit_code"], int) and not isinstance(p["exit_code"], bool), \
        f"INV-3: exit_code 必须为 integer,实际 {type(p['exit_code']).__name__}"
    assert isinstance(p["passed"], bool), \
        f"INV-3: passed 必须为 boolean,实际 {type(p['passed']).__name__}"


def check_inv_4_verdict_well_formed(events, trigger, prior):
    runs = [e for e in events if e["type"] == "verify_run"]
    verdicts = [e for e in events if e["type"] == "review_verdict"]
    assert len(verdicts) == 1, f"INV-4: review_verdict 应恰好 1 个,实际 {len(verdicts)}"
    v = verdicts[0]
    assert v["agent"] == "guide", \
        f"INV-4: review_verdict agent={v['agent']!r} 应为 'guide'(判定权在编排者)"
    assert v["parent_event_id"] == runs[0]["event_id"], \
        f"INV-4: review_verdict parent={v['parent_event_id']} 应链回 verify_run#{runs[0]['event_id']}"
    assert v["payload"]["verdict"] in ("pass", "reject"), \
        f"INV-4: verdict={v['payload']['verdict']!r} 应 ∈ {{pass, reject}}"
    tc_eid = trigger["payload"]["task_card_event_id"]
    task_card = next(
        e["payload"]["task_card"] for e in prior
        if e["type"] == "guide_delegate" and e["event_id"] == tc_eid
    )
    assert v["payload"]["task_id"] == task_card["task_id"], \
        f"INV-4: verdict task_id={v['payload']['task_id']!r} 应为 {task_card['task_id']!r}"


def check_inv_5_machine_judgment_authority(events):
    """灵魂 INV: verify_run.passed == True 当且仅当 review_verdict.verdict == 'pass'。"""
    runs = [e for e in events if e["type"] == "verify_run"]
    verdicts = [e for e in events if e["type"] == "review_verdict"]
    passed = runs[0]["payload"]["passed"]
    verdict = verdicts[0]["payload"]["verdict"]
    if passed:
        assert verdict == "pass", \
            f"INV-5: verify_run.passed=True 但 verdict={verdict!r} (Guide 不可推翻机器结果)"
    else:
        assert verdict != "pass", \
            f"INV-5: verify_run.passed=False 但 verdict='pass' (passed=False 时禁止 pass)"


def check_inv_6_all_schemas_pass(events):
    for e in events:
        validate_event(e)


def check_inv_7_parent_chain_strictly_backward(events, trigger):
    valid_ids = {trigger["event_id"]} | {e["event_id"] for e in events}
    for e in events:
        pid = e.get("parent_event_id")
        assert isinstance(pid, int) and pid >= 1, \
            f"INV-7: event#{e['event_id']} parent_event_id 必须为正整数"
        assert pid in valid_ids, \
            f"INV-7: event#{e['event_id']} parent_event_id={pid} 未链回 trigger 或先前事件"
        assert pid < e["event_id"], \
            f"INV-7: event#{e['event_id']} parent_event_id={pid} 必须指向更早事件(防环)"


# ---- M1.4-1 自洽测试 ----

def test_reference_output_passes_data_scope_invariants():
    """M1.4-1 self-consistency: reference_output 通过所有 scope=='data' 的 INV。"""
    fx = load_fixture("m14_verify_login.json")
    events = fx["reference_output"]
    trigger = fx["trigger"]
    prior = fx["prior_context"]

    data_invs = [inv for inv in fx["invariants"] if inv["scope"] == "data"]
    assert len(data_invs) == 7, f"M1.4-1 期望 7 条 data-scope INV,实际 {len(data_invs)}"

    check_inv_1_verify_run_well_formed(events, trigger)
    check_inv_2_verifier_runs_schema_command_verbatim(events, trigger, prior)
    check_inv_3_verify_run_required_fields(events)
    check_inv_4_verdict_well_formed(events, trigger, prior)
    check_inv_5_machine_judgment_authority(events)
    check_inv_6_all_schemas_pass(events)
    check_inv_7_parent_chain_strictly_backward(events, trigger)
