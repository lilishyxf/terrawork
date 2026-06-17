"""M2.1 审查 context 物理隔离测试(铁律③ / §5 / ADR-002)。

核心断言:assemble_review_context 装配出的审查 context **绝不**含制作者 npc_think /
Guide guide_think 的内容(泄露哨兵),但保留事实(文件/验证结果)。隔离由代码硬过滤强制。
确定性、离线。
"""
import json
from pathlib import Path

from harness.context import assemble_review_context, filter_events_for_review

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def _context_text(messages) -> str:
    return "\n".join(m["content"] for m in messages)


def check_inv_1_narrative_physically_isolated(messages, sentinels):
    text = _context_text(messages)
    for s in sentinels:
        assert s not in text, f"INV-1: 泄露哨兵 {s!r} 出现在审查 context 中(物理隔离失败)"


def check_inv_2_facts_retained(messages):
    text = _context_text(messages)
    assert "login.py" in text, "INV-2: 审查 context 应含 tool_done 的文件事实 login.py"
    assert "passed=True" in text or "passed=true" in text.lower(), \
        "INV-2: 审查 context 应含 verify_run 的 passed 结果"
    assert "import login" in text, "INV-2: 审查 context 应含 verify_run 的命令事实"


def check_inv_3_filter_drops_narrative_types(events):
    filtered = filter_events_for_review(events)
    types = {e["type"] for e in filtered}
    assert "npc_think" not in types, "INV-3: 过滤结果不得含 npc_think"
    assert "guide_think" not in types, "INV-3: 过滤结果不得含 guide_think"
    assert "tool_done" in types and "verify_run" in types, "INV-3: 过滤结果应含事实类型"


def test_review_context_physical_isolation():
    """M2.1 self-consistency: 审查 context 隔离叙述、保留事实。"""
    fx = load_fixture("m2_review_isolation.json")
    messages = assemble_review_context(fx["task_card"], fx["session"], role_name="tailor")

    data_invs = [inv for inv in fx["invariants"] if inv["scope"] == "data"]
    assert len(data_invs) == 3, f"期望 3 条 data-scope INV,实际 {len(data_invs)}"

    check_inv_1_narrative_physically_isolated(messages, fx["leak_sentinels"])
    check_inv_2_facts_retained(messages)
    check_inv_3_filter_drops_narrative_types(fx["session"])


def test_review_context_has_tailor_system_prompt():
    """system message 来自 roles/tailor.md(reviewer 角色),且其正文不带哨兵。"""
    fx = load_fixture("m2_review_isolation.json")
    messages = assemble_review_context(fx["task_card"], fx["session"], role_name="tailor")
    assert messages[0]["role"] == "system"
    assert "裁缝" in messages[0]["content"] or "Tailor" in messages[0]["content"]
