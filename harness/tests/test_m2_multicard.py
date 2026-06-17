"""M2.4 多卡 test-first 分解契约自洽(data-scope)。

锁 Guide 的 test-first 两卡分解结构:测试卡 + 实现卡(depends_on 测试卡)、测试与实现分离、
两卡 verification 意图正确、均通过 task_card.schema。runtime INV(实例分离/共享 worktree/
依赖排序)由 e2e(代码落地后)验证。
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


def test_multicard_testfirst_decomposition_contract():
    fx = load_fixture("m2d_multicard_testfirst.json")
    events = fx["reference_output"]
    cards = _builder_cards(events)

    data_invs = [inv for inv in fx["invariants"] if inv["scope"] == "data"]
    assert len(data_invs) == 5, f"期望 5 条 data-scope INV,实际 {len(data_invs)}"

    # INV-1: 恰好 2 张 builder 卡
    assert len(cards) == 2, f"INV-1: test-first 应 2 张 builder 卡,实际 {len(cards)}"
    by_id = {c["task_id"]: c for c in cards}
    assert "t-login-tests" in by_id and "t-login-impl" in by_id

    tests_card = by_id["t-login-tests"]
    impl_card = by_id["t-login-impl"]

    # INV-2: 实现卡 depends_on 测试卡
    assert impl_card.get("depends_on") == ["t-login-tests"], \
        f"INV-2: 实现卡应 depends_on 测试卡,实际 {impl_card.get('depends_on')}"

    # INV-3: 测试与实现分离(boundaries 声明)
    assert any("不写" in b and "实现" in b for b in tests_card["boundaries"]), \
        "INV-3: 测试卡 boundaries 应声明'不写实现'"
    assert any("不" in b and ("改" in b or "修改" in b) and "测试" in b for b in impl_card["boundaries"]), \
        "INV-3: 实现卡 boundaries 应声明'不改测试'"

    # INV-4: verification 意图——测试卡验 collect、实现卡验通过
    tv = tests_card["verification"][0]
    iv = impl_card["verification"][0]
    assert tv["type"] == "machine_verifiable" and "--collect-only" in tv["command"], \
        "INV-4: 测试卡应验'可 collect'(实现缺失也成立)"
    assert iv["type"] == "machine_verifiable" and "--collect-only" not in iv["command"], \
        "INV-4: 实现卡应验'测试通过'(跑全测试)"
    assert tv["expected"]["exit_code"] == 0 and iv["expected"]["exit_code"] == 0

    # INV-5: 两卡通过 task_card.schema(含 depends_on)
    validate_task_card(tests_card)
    validate_task_card(impl_card)
