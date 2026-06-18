"""M2.7-3 角色感知委派 e2e(runtime INV-4/5,ADR-019)。

INV-4 instantiate_by_specialty:编排器按卡的 assignee_specialty 实例化 <specialty>#N、
建对应 worktree、merge 对应分支,而非写死 merchant。
INV-5 test_author_ne_implementer:同一专长的两张卡(test-first)分到不同实例
(frontend#1 测试 / frontend#2 实现),作者≠实现者仍成立。
INV-7(无 assignee_specialty → merchant 单审查)由既有 m2d/m25 套件覆盖,此处不重复。

离线确定性:stub guide 返预设卡 + scripted builder(各写各文件) + tailor pass。
"""
import json
import re
import subprocess
from pathlib import Path

from harness.session.store import SessionStore
from harness.orchestrator import advance

_HASH = re.compile(r"^[0-9a-f]{7,40}$")


def _card(tid, specialty, mod, deps=None, imports=None):
    c = {
        "task_id": tid, "assignee_role": "builder", "assignee_specialty": specialty,
        "objective": f"实现 {mod}.py", "output_format": f"{mod}.py",
        "allowed_tools": ["read", "write", "bash"], "boundaries": [f"只写 {mod}.py"],
        "verification": [{"type": "machine_verifiable",
                          "command": f"python -c \"import {imports or mod}\"",
                          "expected": {"exit_code": 0}}],
    }
    if deps:
        c["depends_on"] = deps
    return c


def _stub(tasks):
    payload = json.dumps({"thinking": "x", "tasks": tasks}, ensure_ascii=False)

    class _LLM:
        def complete(self, model, messages, **kwargs):
            system = messages[0]["content"] if messages else ""
            if "裁缝" in system or "Tailor" in system:
                return '{"verdict": "pass", "notes": "ok"}'
            return payload
    return _LLM()


def _git_init(repo: Path):
    for c in (["git", "init", "-b", "main"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"], ["git", "commit", "--allow-empty", "-m", "init"]):
        subprocess.run(c, cwd=repo, check=True, capture_output=True)


def _w(mod):
    return [{"tool": "write", "params": {"path": f"{mod}.py", "content": f"# {mod}\nOK=1\n"}}]


def _instances_by_tid(events):
    de = {e["payload"]["task_card"]["task_id"]: e["event_id"]
          for e in events if e["type"] == "guide_delegate"}
    out = {}
    for a in events:
        if a["type"] != "guide_assign":
            continue
        for tid, deid in de.items():
            if a["payload"]["task_card_event_id"] == deid and \
               not a["payload"]["assignee_instance"].startswith(("blaster", "tailor")):
                out[tid] = a["payload"]["assignee_instance"]
    return out


def test_dispatch_by_specialty(tmp_path):
    """INV-4:两张不同专长独立卡 → frontend#1 / backend#1,各自 worktree 与分支,非 merchant。"""
    repo = tmp_path / "repo"; repo.mkdir(); _git_init(repo)
    store = SessionStore(tmp_path / "s.db", session_id="sp")
    store.append_event(agent="user", type="user_command",
                       payload={"text": "做个带界面和接口的应用"}, session_id="sp")
    tasks = [_card("t-ui", "frontend", "ui"), _card("t-api", "backend", "api")]
    report = advance(store, llm_client=_stub(tasks), repo_root=repo,
                     worktrees_base=tmp_path / "wt",
                     builder_scripted_actions={"t-ui": _w("ui"), "t-api": _w("api")},
                     max_concurrent_agents=2)
    events = store.query_session()

    inst = _instances_by_tid(events)
    assert inst == {"t-ui": "frontend#1", "t-api": "backend#1"}, \
        f"INV-4: 应按专长实例化,实际 {inst}"

    wts = sorted(p.name for p in (tmp_path / "wt").iterdir() if p.is_dir())
    assert wts == ["backend-1", "frontend-1"], f"INV-4: worktree 应按专长,实际 {wts}"

    merges = {e["payload"]["task_id"]: e["payload"] for e in events if e["type"] == "merge"}
    assert set(merges) == {"t-ui", "t-api"}
    assert merges["t-ui"]["source"] == "npc/frontend-1"
    assert merges["t-api"]["source"] == "npc/backend-1"
    assert all(_HASH.match(m["commit"]) for m in merges.values())
    assert report["task_board"]["t-ui"]["status"] == "verified_pass"
    assert report["task_board"]["t-api"]["status"] == "verified_pass"


def test_same_specialty_author_ne_implementer(tmp_path):
    """INV-5:同专长 test-first 两卡 → frontend#1(测试)≠ frontend#2(实现)。"""
    repo = tmp_path / "repo"; repo.mkdir(); _git_init(repo)
    store = SessionStore(tmp_path / "s.db", session_id="tf")
    store.append_event(agent="user", type="user_command",
                       payload={"text": "test-first 做个前端功能"}, session_id="tf")
    tasks = [
        _card("t-tests", "frontend", "test_x"),
        _card("t-impl", "frontend", "impl_x", deps=["t-tests"], imports="test_x, impl_x"),
    ]
    report = advance(store, llm_client=_stub(tasks), repo_root=repo,
                     worktrees_base=tmp_path / "wt",
                     builder_scripted_actions={"t-tests": _w("test_x"), "t-impl": _w("impl_x")})
    events = store.query_session()

    inst = _instances_by_tid(events)
    assert inst == {"t-tests": "frontend#1", "t-impl": "frontend#2"}, \
        f"INV-5: 同专长应分到不同实例(作者≠实现者),实际 {inst}"

    # impl 从 main 切自带测试(依赖经 main)+ 本卡实现 → verify import 两者通过
    impl_vr = next(e for e in events if e["type"] == "verify_run"
                   and e["payload"]["task_id"] == "t-impl")
    assert impl_vr["payload"]["passed"] is True
    assert report["task_board"]["t-impl"]["status"] == "verified_pass"
