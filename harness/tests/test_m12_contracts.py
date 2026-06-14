"""M1.2 契约测试 — 验证 Guide 产出满足 m12_login_command.json 的不变量。

M1.2a：用 mock LLM（或 fixture.reference_output），验证校验函数本身正确。
M1.2b：用真实 LLM（DeepSeek/GPT/Claude 三家，ADR-009），产出 = guide_step() 实际返回。

字段命名对齐 M0 ground truth：task_card 用 objective / allowed_tools / verification(数组)；
verification 条件用 machine_verifiable | hitl_escalation。
"""
import json
from pathlib import Path

import pytest

from harness.session.schema import (
    validate_event,
    validate_task_card,
    validate_verification,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name, encoding="utf-8") as f:
        return json.load(f)


# ---- 每条 invariant 一个独立校验函数 ----

def check_inv_1_think_precedes_delegate(events: list[dict]) -> None:
    thinks = [e for e in events if e["type"] == "guide_think"]
    delegates = [e for e in events if e["type"] == "guide_delegate"]
    assert thinks, "INV-1: 至少需要 1 个 guide_think"
    first_think_id = min(t["event_id"] for t in thinks)
    if delegates:
        first_delegate_id = min(d["event_id"] for d in delegates)
        assert first_think_id < first_delegate_id, \
            "INV-1: 第一个 guide_think 必须早于所有 guide_delegate"


def check_inv_2_at_least_one_delegate(events: list[dict]) -> None:
    delegates = [e for e in events if e["type"] == "guide_delegate"]
    assert delegates, "INV-2: 至少需要 1 个 guide_delegate"


def check_inv_3_parent_chain(events: list[dict], trigger_id: int) -> None:
    valid_ids = {trigger_id} | {e["event_id"] for e in events}
    for e in events:
        pid = e.get("parent_event_id")
        assert pid is not None and isinstance(pid, int) and pid >= 1, \
            f"INV-3: event {e['event_id']} parent_event_id 必须为正整数"
        assert pid in valid_ids, \
            f"INV-3: event {e['event_id']} parent_event_id={pid} 未链回 trigger 或先前事件"
        assert pid < e["event_id"], \
            f"INV-3: event {e['event_id']} parent_event_id={pid} 必须指向更早事件（防环）"


def check_inv_4_all_schemas_pass(events: list[dict]) -> None:
    for e in events:
        validate_event(e)  # raises SchemaError if invalid
        if e["type"] == "guide_delegate":
            tc = e["payload"]["task_card"]
            validate_task_card(tc)
            for v in tc["verification"]:        # verification 是数组
                validate_verification(v)


def check_inv_5_verification_constrained(events: list[dict]) -> None:
    for e in events:
        if e["type"] != "guide_delegate":
            continue
        for v in e["payload"]["task_card"]["verification"]:   # 遍历数组
            assert v["type"] in ("machine_verifiable", "hitl_escalation"), \
                f"INV-5: verification.type 必须是 machine_verifiable 或 hitl_escalation，实际 = {v['type']}"
            if v["type"] == "machine_verifiable":
                assert "exit_code" in v.get("expected", {}), \
                    "INV-5: machine_verifiable 必须含结构化 expected.exit_code"
            else:  # hitl_escalation
                assert v.get("reason"), \
                    "INV-5: hitl_escalation 必须含非空 reason 字段"
                assert v.get("acceptance_prompt"), \
                    "INV-5: hitl_escalation 必须含非空 acceptance_prompt 字段"


def check_inv_6_granularity(events: list[dict]) -> None:
    delegates = [e for e in events if e["type"] == "guide_delegate"]
    assert 1 <= len(delegates) <= 6, \
        f"INV-6: 任务卡数量 {len(delegates)} 超出 [1, 6]"
    for d in delegates:
        objective = d["payload"]["task_card"]["objective"]
        assert 10 <= len(objective) <= 500, \
            f"INV-6: task {d['payload']['task_card']['task_id']} objective 长度 {len(objective)} 超出 [10, 500]"


# ---- M1.2a 测试：用 reference_output 跑通校验函数本身 ----

def test_reference_output_passes_all_invariants():
    """M1.2a：确认 fixture 的 reference_output 本身满足全部不变量。
    这是 sanity check — 如果 reference 自己都不过，fixture 设计错了。"""
    fx = load_fixture("m12_login_command.json")
    events = fx["reference_output"]
    trigger_id = fx["trigger"]["event_id"]

    check_inv_1_think_precedes_delegate(events)
    check_inv_2_at_least_one_delegate(events)
    check_inv_3_parent_chain(events, trigger_id)
    check_inv_4_all_schemas_pass(events)
    check_inv_5_verification_constrained(events)
    check_inv_6_granularity(events)


# ---- M1.2b 测试占位：真实 LLM 接入后启用 ----

@pytest.mark.skip(reason="M1.2b — requires LLM provider keys; enable when real Guide is wired")
def test_real_guide_satisfies_invariants_on_three_providers():
    """M1.2b：三家 provider 各跑一次，全部 invariant 都过才算通过（ADR-009）。"""
    pass
