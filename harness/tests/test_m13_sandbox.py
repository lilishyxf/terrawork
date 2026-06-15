"""M1.3 contract tests — sandbox + single NPC execution.

M1.3-1: 仅数据层自洽 (data-scope invariants on reference_output).
M1.3-3: 端到端(mock executor / worktree)将启用 runtime-scope invariants.
"""
import json
from pathlib import Path

from harness.session.schema import validate_event

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name, encoding="utf-8") as f:
        return json.load(f)


# ---- INV-2 ~ INV-7：data-scope 校验函数（对照 m13 reference_output） ----

def check_inv_2_tool_events_paired_via_parent(events: list[dict]) -> None:
    """每个 tool_intent 恰好一个 tool_done 配对：parent 链 + 同 agent + 同 tool。严格 1:1。"""
    intents = [e for e in events if e["type"] == "tool_intent"]
    dones = [e for e in events if e["type"] == "tool_done"]
    for it in intents:
        matched = [d for d in dones if d["parent_event_id"] == it["event_id"]]
        assert len(matched) == 1, \
            f"INV-2: tool_intent#{it['event_id']} 应恰好 1 个配对 tool_done，实际 {len(matched)}"
        d = matched[0]
        assert d["agent"] == it["agent"], \
            f"INV-2: tool_done#{d['event_id']} agent 与配对 intent 不一致"
        assert d["payload"]["tool"] == it["payload"]["tool"], \
            f"INV-2: tool_done#{d['event_id']} tool 与配对 intent 不一致"
    for d in dones:
        parents = [it for it in intents if it["event_id"] == d["parent_event_id"]]
        assert len(parents) == 1, \
            f"INV-2: tool_done#{d['event_id']} 应恰好链回 1 个 tool_intent（无孤儿/无 N:1）"
    assert len(intents) == len(dones), \
        f"INV-2: tool_intent({len(intents)}) 与 tool_done({len(dones)}) 数量应相等（严格 1:1）"


def check_inv_3_parent_chain_strictly_backward(events: list[dict], trigger: dict) -> None:
    """parent_event_id 正整数、链回 trigger 或先前事件、严格 < 自身 event_id（防环）。"""
    valid_ids = {trigger["event_id"]} | {e["event_id"] for e in events}
    for e in events:
        pid = e.get("parent_event_id")
        assert isinstance(pid, int) and pid >= 1, \
            f"INV-3: event#{e['event_id']} parent_event_id 必须为正整数"
        assert pid in valid_ids, \
            f"INV-3: event#{e['event_id']} parent_event_id={pid} 未链回 trigger 或先前事件"
        assert pid < e["event_id"], \
            f"INV-3: event#{e['event_id']} parent_event_id={pid} 必须指向更早事件（防环）"


def check_inv_4_agent_field_consistent(events: list[dict], trigger: dict) -> None:
    """tool_intent / tool_done / review_request 的 agent 必须 == assignee_instance。"""
    inst = trigger["payload"]["assignee_instance"]
    for e in events:
        if e["type"] in ("tool_intent", "tool_done", "review_request"):
            assert e["agent"] == inst, \
                f"INV-4: event#{e['event_id']} agent={e['agent']!r} 应为实例 {inst!r}"


def check_inv_5_tool_within_whitelist(events: list[dict], prior: list[dict]) -> None:
    """tool_intent.payload.tool 必须在 prior_context 任务卡的 allowed_tools 内。"""
    allowed: set[str] = set()
    for e in prior:
        if e["type"] == "guide_delegate":
            allowed |= set(e["payload"]["task_card"]["allowed_tools"])
    assert allowed, "INV-5: prior_context 未找到任何 task_card.allowed_tools"
    for e in events:
        if e["type"] == "tool_intent":
            tool = e["payload"]["tool"]
            assert tool in allowed, \
                f"INV-5: event#{e['event_id']} tool {tool!r} 不在白名单 {sorted(allowed)}"


def check_inv_6_all_schemas_pass(events: list[dict]) -> None:
    """所有事件过 events.schema.json；tool_done 含 status；review_request 含三字段。"""
    for e in events:
        validate_event(e)  # raises SchemaError if invalid
        if e["type"] == "tool_done":
            assert e["payload"].get("status") in ("ok", "error"), \
                f"INV-6: tool_done#{e['event_id']} status 必须 ∈ {{ok, error}}"
        if e["type"] == "review_request":
            for field in ("task_id", "reviewer", "artifact"):
                assert field in e["payload"], \
                    f"INV-6: review_request#{e['event_id']} 缺字段 {field}"


def check_inv_7_completion_signal(events: list[dict], trigger: dict, prior: list[dict]) -> None:
    """恰好 1 个 review_request，parent 链回 trigger，task_id == 任务卡 task_id。"""
    reqs = [e for e in events if e["type"] == "review_request"]
    assert len(reqs) == 1, f"INV-7: review_request 应恰好 1 个，实际 {len(reqs)}"
    req = reqs[0]
    assert req["parent_event_id"] == trigger["event_id"], \
        f"INV-7: review_request parent={req['parent_event_id']} 应链回 trigger#{trigger['event_id']}"
    tc_eid = trigger["payload"]["task_card_event_id"]
    task_card = next(
        e["payload"]["task_card"] for e in prior
        if e["type"] == "guide_delegate" and e["event_id"] == tc_eid
    )
    assert req["payload"]["task_id"] == task_card["task_id"], \
        f"INV-7: review_request task_id={req['payload']['task_id']!r} 应为 {task_card['task_id']!r}"


# ---- M1.3-1 自洽测试：纯数据层 ----

def test_reference_output_passes_data_scope_invariants():
    """M1.3-1 自洽 sanity check：reference_output 通过所有 scope=='data' 的 INV。

    通过此测试不代表 sandbox 实跑能产出同样事件流；只代表 fixture 内部一致。
    """
    fx = load_fixture("m13_merchant_login_impl.json")
    events = fx["reference_output"]
    trigger = fx["trigger"]
    prior = fx["prior_context"]

    data_invs = [inv for inv in fx["invariants"] if inv["scope"] == "data"]
    assert len(data_invs) == 6, f"M1.3-1 期望 6 条 data-scope INV，实际 {len(data_invs)}"

    check_inv_2_tool_events_paired_via_parent(events)
    check_inv_3_parent_chain_strictly_backward(events, trigger)
    check_inv_4_agent_field_consistent(events, trigger)
    check_inv_5_tool_within_whitelist(events, prior)
    check_inv_6_all_schemas_pass(events)
    check_inv_7_completion_signal(events, trigger, prior)
