"""M2.7 角色感知专家委派契约自洽(data-scope,ADR-019)。

锁:带 assignee_specialty 的卡过 task_card.schema、去掉该字段仍合法(向后兼容)、向导匹配到
≥3 个不同专长、每个专长属于锁定 builder 集。按专长实例化/双审查(runtime)由 e2e(M2.7-3/4)验证。
"""
import json
from pathlib import Path

import pytest

from harness.session.schema import validate_task_card, SchemaError

FIXTURE_DIR = Path(__file__).parent / "fixtures"

# ADR-019 锁定的 builder 专长集
BUILDER_SPECIALTIES = {
    "frontend", "backend", "database", "desktop_shell",
    "ai_engineer", "rapid_proto", "tech_writer",
}


def load_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _builder_cards(events):
    return [e["payload"]["task_card"] for e in events
            if e["type"] == "guide_delegate"
            and e["payload"]["task_card"].get("assignee_role") == "builder"]


def test_specialist_delegation_contract():
    fx = load_fixture("m27_specialist_delegation.json")
    cards = _builder_cards(fx["reference_output"])

    data_invs = [inv for inv in fx["invariants"] if inv["scope"] == "data"]
    assert len(data_invs) == 3, f"期望 3 条 data-scope INV,实际 {len(data_invs)}"

    # INV-1: 每张带 assignee_specialty 的卡过 schema
    assert len(cards) == 4, f"期望 4 张 builder 卡,实际 {len(cards)}"
    for c in cards:
        assert "assignee_specialty" in c, f"{c['task_id']} 应带 assignee_specialty"
        validate_task_card(c)

    # INV-2: 匹配到 ≥3 个不同专长(非全塞一个默认 builder)
    specialties = {c["assignee_specialty"] for c in cards}
    assert len(specialties) >= 3, f"INV-2: 应匹配到 ≥3 个不同专长,实际 {specialties}"
    assert specialties == {"frontend", "backend", "database", "desktop_shell"}

    # INV-3: 每个专长属于锁定 builder 集
    assert specialties <= BUILDER_SPECIALTIES, \
        f"INV-3: 专长须在 roster 内,越界 {specialties - BUILDER_SPECIALTIES}"


def test_assignee_specialty_optional_backward_compat():
    """去掉 assignee_specialty 的卡仍过 schema(向后兼容:缺省→merchant)。"""
    fx = load_fixture("m27_specialist_delegation.json")
    card = dict(_builder_cards(fx["reference_output"])[0])
    card.pop("assignee_specialty")
    validate_task_card(card)  # 不应抛


def test_assignee_specialty_rejects_bad_slug():
    """assignee_specialty 须满足实例 slug 前缀正则;非法值应被 schema 拒。"""
    fx = load_fixture("m27_specialist_delegation.json")
    card = dict(_builder_cards(fx["reference_output"])[0])
    card["assignee_specialty"] = "Frontend#1"  # 大写 + #,非法
    with pytest.raises(SchemaError):
        validate_task_card(card)
