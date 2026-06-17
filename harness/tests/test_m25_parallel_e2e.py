"""M2.5 并行多实例编排 e2e(runtime INV-4/5/6/7,ADR-018)。

两张互不依赖的 builder 卡(mod_a/mod_b)同时 ready → 编排器并行批派发(各自实例/worktree/
子进程并发)。
- INV-4 concurrent_batch_dispatch:两 guide_assign 在任一 review_request 之前成对出现。
- INV-5 both_merge:两卡都 verified_pass + 真 merge。
- INV-6 actual_parallelism + INV-7 max_concurrent:各 builder sleep ~1s,cap=2(并行)
  墙钟显著短于 cap=1(被上限串行化)→ 直接证真并发 + 上限生效。

确定性:builder 走 scripted_actions(子进程端不调 LLM);guide/tailor stub 跑编排器进程内。
"""
import json
import re
import subprocess
import time
from pathlib import Path

from harness.session.store import SessionStore
from harness.orchestrator import advance

_HASH = re.compile(r"^[0-9a-f]{7,40}$")


def _card(tid, mod):
    return {
        "task_id": tid, "assignee_role": "builder",
        "objective": f"实现 {mod}.py", "output_format": f"{mod}.py",
        "allowed_tools": ["read", "write", "bash"], "boundaries": [f"只写 {mod}.py"],
        "verification": [{"type": "machine_verifiable",
                          "command": f"python -c \"import {mod}\"",
                          "expected": {"exit_code": 0}}],
    }


_CARD_A = _card("t-mod-a", "mod_a")
_CARD_B = _card("t-mod-b", "mod_b")
_GUIDE_2INDEP = json.dumps({"thinking": "两独立模块", "tasks": [_CARD_A, _CARD_B]},
                           ensure_ascii=False)


class _StubLLM:
    def complete(self, model, messages, **kwargs):
        system = messages[0]["content"] if messages else ""
        if "裁缝" in system or "Tailor" in system:
            return '{"verdict": "pass", "notes": "ok"}'
        return _GUIDE_2INDEP


def _git_init_repo(repo: Path):
    for c in (["git", "init", "-b", "main"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"], ["git", "commit", "--allow-empty", "-m", "init"]):
        subprocess.run(c, cwd=repo, check=True, capture_output=True)


def _actions(mod, sleep_s=0.0):
    acts = []
    if sleep_s:
        acts.append({"tool": "bash",
                     "params": {"cmd": f"python -c \"import time; time.sleep({sleep_s})\""},
                     "task_id": f"t-{mod.replace('_', '-')}"})
    acts.append({"tool": "write", "params": {"path": f"{mod}.py", "content": f"# {mod}\nOK = True\n"},
                 "task_id": f"t-{mod.replace('_', '-')}"})
    return acts


def _run(tmp_path, *, sleep_s=0.0, max_concurrent=10):
    repo = tmp_path / "repo"; repo.mkdir(parents=True); _git_init_repo(repo)
    store = SessionStore(tmp_path / "session.db", session_id="par")
    store.append_event(agent="user", type="user_command",
                       payload={"text": "搭两个独立模块"}, session_id="par")
    scripted = {"t-mod-a": _actions("mod_a", sleep_s), "t-mod-b": _actions("mod_b", sleep_s)}
    t0 = time.perf_counter()
    report = advance(store, llm_client=_StubLLM(), repo_root=repo,
                     worktrees_base=tmp_path / "worktrees",
                     builder_scripted_actions=scripted,
                     max_concurrent_agents=max_concurrent)
    elapsed = time.perf_counter() - t0
    return store, report, elapsed


def test_parallel_both_merge_and_batch_dispatch(tmp_path):
    """INV-4 + INV-5:并发批派发(两 guide_assign 先于任一 review_request)+ 两卡都 merge。"""
    store, report, _ = _run(tmp_path, max_concurrent=2)
    events = store.query_session()

    # INV-4: 两 builder guide_assign 的 event_id 都 < 任一 review_request(批先派后完成)
    builder_assigns = [e["event_id"] for e in events if e["type"] == "guide_assign"
                       and e["payload"]["assignee_instance"].startswith("merchant#")]
    review_reqs = [e["event_id"] for e in events if e["type"] == "review_request"]
    assert len(builder_assigns) == 2 and len(review_reqs) == 2
    assert max(builder_assigns) < min(review_reqs), \
        "INV-4: 两卡应在同一并行批先派发(guide_assign 早于任一 review_request)"

    # 各自独立 worktree
    wts = sorted(p.name for p in (tmp_path / "worktrees").iterdir() if p.is_dir())
    assert wts == ["merchant-1", "merchant-2"], f"应两独立 worktree,实际 {wts}"

    # INV-5: 两卡 verified_pass + 真 merge
    assert report["task_board"]["t-mod-a"]["status"] == "verified_pass"
    assert report["task_board"]["t-mod-b"]["status"] == "verified_pass"
    merges = [e for e in events if e["type"] == "merge"]
    assert len(merges) == 2
    for m in merges:
        assert m["payload"]["result"] == "success" and _HASH.match(m["payload"]["commit"])


def test_parallel_speedup_vs_concurrency_cap(tmp_path):
    """INV-6 + INV-7:各 builder sleep 1s,cap=2(并行)墙钟显著短于 cap=1(上限串行化)。"""
    _, _, parallel = _run(tmp_path / "p", sleep_s=1.0, max_concurrent=2)
    _, _, serial = _run(tmp_path / "s", sleep_s=1.0, max_concurrent=1)

    # cap=1 被串行化(≈2 sleep),cap=2 并发(≈1 sleep);差值应 > 半个 sleep(机器无关的相对判据)
    assert serial - parallel > 0.5, \
        f"INV-6/7: 并行应显著快于上限=1 的串行;parallel={parallel:.2f}s serial={serial:.2f}s"
