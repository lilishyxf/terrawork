"""M2.4 多卡 test-first 编排 e2e + M2.6-A 真 merge 隔离(ADR-016)。

证:测试卡→merchant#1、实现卡→merchant#2(作者≠实现者,M2.4 INV-6);impl 在 tests
merge 后才派(depends_on 排序,M2.4 INV-8)。
ADR-016 升级后:两卡各自独立 worktree(merchant-1/merchant-2,非共享 feature-1,INV-1);
产物只经真 git merge 到 main 流动——依赖卡从 main 切自带测试(INV-3),verifier 在独立
detached 验证 worktree 内跑(merge-then-verify,INV-4),merge 事件带真实 commit(INV-5)。
离线确定性:stub guide 返两卡 + scripted builder(各写各的文件) + tailor pass。
验证命令用 stdlib import(不依赖 pytest);impl 卡 verify 同时 import 测试文件(经 main
继承)+ 实现文件(本卡提交),都成功 → 机器证 INV-3 + INV-4。
"""
import json
import subprocess
from pathlib import Path

from harness.session.store import SessionStore
from harness.orchestrator import advance

# 两卡 test-first:测试卡(写 mytests.py)+ 实现卡(depends_on,写 myimpl.py)
_TESTS_CARD = {
    "task_id": "t-tests", "assignee_role": "builder",
    "objective": "写测试 mytests.py", "output_format": "mytests.py",
    "allowed_tools": ["read", "write", "bash"], "boundaries": ["只写测试,不写实现"],
    "verification": [{"type": "machine_verifiable",
                      "command": "python -c \"import mytests\"",
                      "expected": {"exit_code": 0}}],
}
_IMPL_CARD = {
    "task_id": "t-impl", "assignee_role": "builder",
    "objective": "实现 myimpl.py 让测试通过", "output_format": "myimpl.py",
    "allowed_tools": ["read", "write", "bash"], "boundaries": ["不改测试"],
    "depends_on": ["t-tests"],
    # 同时 import 测试文件(经 main 继承)+ 实现文件(本卡提交):证依赖经 main + merge-then-verify
    "verification": [{"type": "machine_verifiable",
                      "command": "python -c \"import mytests, myimpl\"",
                      "expected": {"exit_code": 0}}],
}
_GUIDE_2CARD = json.dumps({"thinking": "test-first 两卡", "tasks": [_TESTS_CARD, _IMPL_CARD]},
                          ensure_ascii=False)
_BUILDER_ACTIONS = {
    "t-tests": [{"tool": "write", "params": {"path": "mytests.py", "content": "# tests\nASSERTED = True\n"},
                 "task_id": "t-tests"}],
    "t-impl": [{"tool": "write", "params": {"path": "myimpl.py", "content": "# impl\nVALUE = 1\n"},
                "task_id": "t-impl"}],
}


class _StubLLM:
    def complete(self, model, messages, **kwargs):
        system = messages[0]["content"] if messages else ""
        if "裁缝" in system or "Tailor" in system:
            return '{"verdict": "pass", "notes": "ok"}'
        return _GUIDE_2CARD


def _git_init_repo(repo: Path):
    for c in (["git", "init", "-b", "main"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"], ["git", "commit", "--allow-empty", "-m", "init"]):
        subprocess.run(c, cwd=repo, check=True, capture_output=True)


def test_multicard_testfirst_e2e(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir(); _git_init_repo(repo)
    store = SessionStore(tmp_path / "session.db")
    store.append_event(agent="user", type="user_command",
                       payload={"text": "test-first 做个功能"}, session_id="mc")
    report = advance(store, llm_client=_StubLLM(), repo_root=repo,
                     worktrees_base=tmp_path / "worktrees",
                     builder_scripted_actions=_BUILDER_ACTIONS)

    events = store.query_session()
    # 两卡都走完到 merge
    merges = {e["payload"]["task_id"] for e in events if e["type"] == "merge"}
    assert merges == {"t-tests", "t-impl"}, f"两卡均应 merge,实际 {merges}"

    # builder guide_assign:task_card_event_id → assignee_instance
    de_by_tid = {e["payload"]["task_card"]["task_id"]: e["event_id"]
                 for e in events if e["type"] == "guide_delegate"}
    builder_assigns = [e for e in events if e["type"] == "guide_assign"
                       and e["payload"]["assignee_instance"].startswith("merchant#")]
    inst_of = {}
    for a in builder_assigns:
        for tid, deid in de_by_tid.items():
            if a["payload"]["task_card_event_id"] == deid:
                inst_of[tid] = a["payload"]["assignee_instance"]

    # INV-6: 测试作者 ≠ 实现者
    assert inst_of["t-tests"] == "merchant#1" and inst_of["t-impl"] == "merchant#2", \
        f"INV-6: 作者≠实现者期望 tests=merchant#1/impl=merchant#2,实际 {inst_of}"

    # ADR-016 INV-1 isolated_worktree:两卡各自独立 worktree(非共享 feature-1);
    # 验证 worktree(verify-*)用后即销 → 仅剩两个 builder worktree。
    wts = sorted(p.name for p in (tmp_path / "worktrees").iterdir() if p.is_dir())
    assert wts == ["merchant-1", "merchant-2"], \
        f"INV-1: 应两个独立 worktree merchant-1/merchant-2(ADR-016 撤共享 feature-1),实际 {wts}"

    # INV-3 dep_via_main + INV-4 merge_then_verify:impl 的 verify 在独立验证 worktree
    # (detached 签出 npc/merchant-2)内 import mytests(经 main 继承)+ myimpl(本卡提交)
    # 并通过 → 证依赖经 main 流动、验证在隔离签出拿到全部产物。
    impl_vr = next(e for e in events if e["type"] == "verify_run"
                   and e["payload"]["task_id"] == "t-impl")
    assert impl_vr["payload"]["passed"] is True, \
        "INV-3/4: impl verify(import 测试+实现)应通过=依赖经 main + merge-then-verify"

    # INV-5 real_merge_to_main:两条 merge 带真实 commit hash、source 为 per-instance 分支
    import re as _re
    _hash = _re.compile(r"^[0-9a-f]{7,40}$")
    merge_evs = [e for e in events if e["type"] == "merge"]
    assert len(merge_evs) == 2
    for e in merge_evs:
        p = e["payload"]
        assert p["result"] == "success" and _hash.match(p.get("commit", "")), \
            f"INV-5: merge 应 success + 真实 commit,实际 {p}"
        assert p["source"].startswith("npc/merchant-") and p["target"] == "main", p

    # INV-8: impl 的 builder guide_assign 在 tests 的 merge 之后(depends_on 排序)
    tests_merge_eid = next(e["event_id"] for e in events
                           if e["type"] == "merge" and e["payload"]["task_id"] == "t-tests")
    impl_assign_eid = next(a["event_id"] for a in builder_assigns
                           if a["payload"]["task_card_event_id"] == de_by_tid["t-impl"])
    assert impl_assign_eid > tests_merge_eid, \
        f"INV-8: impl 派发({impl_assign_eid})应在 tests merge({tests_merge_eid})之后"

    # 任务板:两卡 verified_pass
    assert report["task_board"]["t-tests"]["status"] == "verified_pass"
    assert report["task_board"]["t-impl"]["status"] == "verified_pass"
