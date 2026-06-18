"""M3-1 View 动画投影测试(ADR-020)。

INV-1 无表外状态;INV-2 happy 流游标快照;INV-3 返工;INV-4 hitl/error;INV-5 实例→sprite-key。
"""
import json
from pathlib import Path

from harness.view.projection import project

FIXTURE = Path(__file__).parent / "fixtures" / "m3_projection.json"
PROTO = json.loads((Path(__file__).resolve().parents[2] / "docs" / "contracts"
                    / "animation_protocol.json").read_text(encoding="utf-8"))
VALID_STATES = set(PROTO["states"])
VALID_ZONES = set(PROTO["zones"])


def _fx():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _upto(events, after_event_id):
    return [e for e in events if e["event_id"] <= after_event_id]


def test_no_states_outside_protocol():
    """INV-1:投影产出的所有 state/zone 都在协议表内(ADR-003 无表外动画)。"""
    fx = _fx()
    snap = project(fx["events"])
    for npc_id, s in snap["npcs"].items():
        assert s["state"] in VALID_STATES, f"{npc_id} 表外 state {s['state']}"
        assert s["zone"] in VALID_ZONES, f"{npc_id} 表外 zone {s['zone']}"


def test_happy_flow_checkpoints():
    """INV-2:单卡双审查流的关键游标快照正确。"""
    fx = _fx()
    for cp in fx["checkpoints"]:
        snap = project(_upto(fx["events"], cp["after_event_id"]))
        for npc_id, exp in cp["expect"].items():
            got = snap["npcs"].get(npc_id)
            assert got is not None, f"游标 {cp['after_event_id']}:缺 {npc_id}"
            assert got["state"] == exp["state"] and got["zone"] == exp["zone"], \
                f"游标 {cp['after_event_id']} {npc_id}: 期望 {exp},实际 {{'state':{got['state']!r},'zone':{got['zone']!r}}}"
    # merge 后 last_merge 置位(钟楼敲钟)
    final = project(fx["events"])
    assert final["last_merge"] and final["last_merge"]["task_id"] == fx["expect_last_merge_task"]
    assert final["cursor"] == fx["events"][-1]["event_id"]


def test_rework_state():
    """INV-3:同一 builder 实例再次 guide_assign → rework(非 working)。"""
    events = [
        {"event_id": 1, "type": "guide_delegate", "agent": "guide", "payload": {"task_card": {"task_id": "t"}}},
        {"event_id": 2, "type": "guide_assign", "agent": "guide", "payload": {"task_card_event_id": 1, "assignee_instance": "merchant#1"}},
        {"event_id": 3, "type": "review_request", "agent": "merchant#1", "payload": {"task_id": "t"}},
        # reject 后返工:同实例再次派
        {"event_id": 4, "type": "guide_assign", "agent": "guide", "payload": {"task_card_event_id": 1, "assignee_instance": "merchant#1"}},
    ]
    snap = project(events)
    assert snap["npcs"]["merchant#1"]["state"] == "rework"
    assert snap["npcs"]["merchant#1"]["zone"] == "workshop"


def test_hitl_and_error():
    """INV-4:hitl_request → guide hitl@at_glass;error → 该 NPC error 且保留原区。"""
    hitl = project([{"event_id": 1, "type": "hitl_request", "agent": "guide",
                     "payload": {"task_id": "t", "reason": "x", "question": "?"}}])
    assert hitl["npcs"]["guide"]["state"] == "hitl"
    assert hitl["npcs"]["guide"]["zone"] == "at_glass"

    err = project([
        {"event_id": 1, "type": "guide_delegate", "agent": "guide", "payload": {"task_card": {"task_id": "t"}}},
        {"event_id": 2, "type": "guide_assign", "agent": "guide", "payload": {"task_card_event_id": 1, "assignee_instance": "merchant#1"}},
        {"event_id": 3, "type": "tool_intent", "agent": "merchant#1", "payload": {"tool": "bash"}},
        {"event_id": 4, "type": "error", "agent": "merchant#1", "payload": {"message": "boom"}},
    ])
    assert err["npcs"]["merchant#1"]["state"] == "error"
    assert err["npcs"]["merchant#1"]["zone"] == "workshop"  # error 保留原区(冒烟叠加)


def test_instance_to_sprite_key():
    """INV-5:实例 → sprite-key 与 kind 映射。"""
    events = [
        {"event_id": 1, "type": "guide_delegate", "agent": "guide", "payload": {"task_card": {"task_id": "t"}}},
        {"event_id": 2, "type": "guide_assign", "agent": "guide", "payload": {"task_card_event_id": 1, "assignee_instance": "frontend#1"}},
    ]
    snap = project(events)
    assert snap["npcs"]["frontend#1"]["sprite_key"] == "frontend"
    assert snap["npcs"]["frontend#1"]["kind"] == "builder"
    assert snap["npcs"]["guide"]["sprite_key"] == "guide" and snap["npcs"]["guide"]["kind"] == "guide"
    assert snap["npcs"]["tailor#1"]["sprite_key"] == "tailor" and snap["npcs"]["tailor#1"]["kind"] == "reviewer"
    assert snap["npcs"]["blaster#1"]["kind"] == "verifier"
