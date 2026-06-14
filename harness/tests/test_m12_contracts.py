"""M1.2 契约测试 — 验证 Guide 产出满足 m12_login_command.json 的不变量。

M1.2a：用 mock LLM（或 fixture.reference_output），验证校验函数本身正确。
M1.2b：用真实 LLM（DeepSeek/GPT/Claude 三家，ADR-009），产出 = guide_step() 实际返回。

字段命名对齐 M0 ground truth：task_card 用 objective / allowed_tools / verification(数组)；
verification 条件用 machine_verifiable | hitl_escalation。
"""
import json
import os
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


def test_guide_step_with_mock_llm_satisfies_invariants():
    """M1.2a-3 验收信号:用 mock LLM 跑真实 guide_step,产出满足 INV-1~6。

    证明 Guide 代码骨架、解析层、schema 校验门、重试循环全部到位,
    只差把 mock 换成真 LLM(M1.2b)。
    """
    from harness.guide import guide_step

    fx = load_fixture("m12_login_command.json")
    trigger = fx["trigger"]
    prior = fx["prior_context"]

    # 默认 mock(TERRA_LLM_MODE 未设)
    events = guide_step(
        session_events=prior + [trigger],
        trigger_event=trigger,
    )

    # mock happy path 不应触发 hitl_request 兜底
    assert events[0]["type"] != "hitl_request", (
        f"Mock happy path 触发了 hitl 兜底,说明重试用尽。"
        f"last_error={events[0]['payload'].get('last_error')}"
    )

    # 跑 INV-1~6(同 reference 那套校验函数)
    trigger_id = trigger["event_id"]
    check_inv_1_think_precedes_delegate(events)
    check_inv_2_at_least_one_delegate(events)
    check_inv_3_parent_chain(events, trigger_id)
    check_inv_4_all_schemas_pass(events)
    check_inv_5_verification_constrained(events)
    check_inv_6_granularity(events)


# ---- M1.2b 验收信号：真实 LLM provider（各自 KEY 门控） ----

@pytest.mark.parametrize("provider_model", [
    pytest.param(
        "deepseek/deepseek-chat",
        marks=pytest.mark.skipif(
            not os.getenv("DEEPSEEK_API_KEY"),
            reason="DEEPSEEK_API_KEY not set in environment / .env"
        ),
        id="deepseek",
    ),
    pytest.param(
        "openai/gpt-4o",
        marks=pytest.mark.skipif(
            not os.getenv("OPENAI_API_KEY"),
            reason="OPENAI_API_KEY not set in environment / .env"
        ),
        id="openai-gpt4o",
    ),
    pytest.param(
        "anthropic/claude-sonnet-4-6",
        marks=pytest.mark.skipif(
            not os.getenv("ANTHROPIC_API_KEY"),
            reason="ANTHROPIC_API_KEY not set in environment / .env"
        ),
        id="anthropic-sonnet-4-6",
    ),
])
def test_real_guide_satisfies_invariants_per_provider(provider_model, monkeypatch):
    """M1.2b 验收信号:在配置了 key 的真实 LLM provider 上跑 guide_step,
    验证 INV-1~6 都成立。

    跑法:
      - 默认 `pytest harness/tests/`: 每家 provider 一个 case,有 key 的跑、没 key 的 skip
      - 排除真实 LLM 测试: `pytest harness/tests/ -k "not real_guide"`
      - 只跑某一家:        `pytest harness/tests/ -k "real_guide and deepseek"`
    """
    from harness.guide import guide_step

    # 切到 real LLM client(monkeypatch 测试结束自动还原)
    monkeypatch.setenv("TERRA_LLM_MODE", "real")

    fx = load_fixture("m12_login_command.json")
    trigger = fx["trigger"]
    prior = fx["prior_context"]

    events = guide_step(
        session_events=prior + [trigger],
        trigger_event=trigger,
        model=provider_model,  # 显式指定,覆盖 roles/guide.md 的默认
    )

    # 真 LLM 可能触发 hitl 兜底——明确报告,而非沉默通过
    if events[0]["type"] == "hitl_request":
        p = events[0]["payload"]
        pytest.fail(
            f"Provider {provider_model} 重试 3 次仍未通过 schema 校验。\n"
            f"  reason: {p.get('reason')}\n"
            f"  question: {p.get('question')}"
        )

    # 跑 INV-1~6(复用既有校验函数)
    trigger_id = trigger["event_id"]
    check_inv_1_think_precedes_delegate(events)
    check_inv_2_at_least_one_delegate(events)
    check_inv_3_parent_chain(events, trigger_id)
    check_inv_4_all_schemas_pass(events)
    check_inv_5_verification_constrained(events)
    check_inv_6_granularity(events)
