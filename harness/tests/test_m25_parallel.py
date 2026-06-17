"""M2.5 并行多卡分解契约自洽(data-scope,ADR-018)。

锁:恰好 2 张 builder 卡、互不依赖(无 depends_on、不互相引用)、verification 各验自身、
均过 task_card.schema。并发派发/真并行/max_concurrent(runtime)由 e2e(test_m25_parallel_e2e)验证。
"""
import json
from pathlib import Path

from harness.session.schema import validate_task_card

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _builder_cards(events):
    return [e["payload"]["task_card"] for e in events
            if e["type"] == "guide_delegate"
            and e["payload"]["task_card"].get("assignee_role") == "builder"]


def test_parallel_independent_decomposition_contract():
    fx = load_fixture("m25_parallel_independent.json")
    cards = _builder_cards(fx["reference_output"])

    data_invs = [inv for inv in fx["invariants"] if inv["scope"] == "data"]
    assert len(data_invs) == 3, f"期望 3 条 data-scope INV,实际 {len(data_invs)}"

    # INV-1: 恰好 2 张 builder 卡,互不依赖
    assert len(cards) == 2, f"INV-1: 应 2 张 builder 卡,实际 {len(cards)}"
    ids = {c["task_id"] for c in cards}
    assert ids == {"t-stringutil", "t-mathutil"}
    for c in cards:
        # 无 depends_on(或不引用对方)
        deps = c.get("depends_on", [])
        assert not (set(deps) & ids), f"INV-1: {c['task_id']} 不应依赖对方卡,实际 depends_on={deps}"

    # INV-2: verification 各验自身、互不引用对方模块
    by_id = {c["task_id"]: c for c in cards}
    s_cmd = by_id["t-stringutil"]["verification"][0]["command"]
    m_cmd = by_id["t-mathutil"]["verification"][0]["command"]
    assert "stringutil" in s_cmd and "mathutil" not in s_cmd, "INV-2: stringutil 卡只验自身"
    assert "mathutil" in m_cmd and "stringutil" not in m_cmd, "INV-2: mathutil 卡只验自身"

    # INV-3: 两卡过 schema
    for c in cards:
        validate_task_card(c)
