"""M2.6-A merge 事件契约自洽(data-scope,ADR-016)。

锁真 git-merge 后 merge 事件的 post-M2.6 形态:result=success、commit 真实 hash、
source=builder 分支(npc/merchant-N)、target=main,且过 events.schema 的 p_merge。
隔离/依赖可见性/merge-then-verify 等 runtime INV 由 A3 e2e(test_m26_merge_e2e)验证。
"""
import json
import re
from pathlib import Path

from harness.session.schema import validate_event

FIXTURE_DIR = Path(__file__).parent / "fixtures"
_HASH_RE = re.compile(r"^[0-9a-f]{7,40}$")


def load_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name, encoding="utf-8") as f:
        return json.load(f)


def test_m26_merge_event_contract():
    fx = load_fixture("m26_merge_isolation.json")
    merges = [e for e in fx["reference_output"] if e["type"] == "merge"]

    data_invs = [inv for inv in fx["invariants"] if inv["scope"] == "data"]
    assert len(data_invs) == 2, f"期望 2 条 data-scope INV,实际 {len(data_invs)}"

    # 两条 merge:测试卡 + 实现卡
    assert len(merges) == 2, f"期望 2 条 merge 事件,实际 {len(merges)}"
    by_tid = {m["payload"]["task_id"]: m["payload"] for m in merges}
    assert set(by_tid) == {"t-login-tests", "t-login-impl"}

    for tid, p in by_tid.items():
        # INV-7: 过 p_merge schema
        validate_event(next(m for m in merges if m["payload"]["task_id"] == tid))
        # INV-5: post-M2.6 真 merge 形态
        assert p["result"] == "success", f"{tid}: result 应 success"
        assert p["target"] == "main", f"{tid}: target 应 main"
        assert p["source"].startswith("npc/merchant-"), \
            f"{tid}: source 应为 per-instance 分支 npc/merchant-N(ADR-016 撤 ADR-015 共享),实际 {p['source']}"
        assert _HASH_RE.match(p["commit"]), f"{tid}: commit 应为真实 hash 形态,实际 {p['commit']}"

    # 测试卡 → merchant-1、实现卡 → merchant-2(per-card 隔离分支)
    assert by_tid["t-login-tests"]["source"] == "npc/merchant-1"
    assert by_tid["t-login-impl"]["source"] == "npc/merchant-2"
