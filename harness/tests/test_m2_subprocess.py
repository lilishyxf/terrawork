"""M2.6-B NPC 子进程隔离测试(ADR-017)。

INV-1/2: 子进程执行 builder(scripted)→ 事件经同一 SQLite 旁路落盘(多写者 WAL)。
INV-3:   child 崩溃(坏 trigger)→ parent 抛 NpcSubprocessError、主进程存活、无半产物。
INV-4:   advance(npc_in_subprocess=True) 单卡 scripted 与进程内等价终态(verified_pass + 真 merge)。

确定性:builder 走 scripted_actions(JSON 跨界,子进程端不调 LLM);guide/tailor 的 stub
LLM 跑在编排器进程内(INV-4),不过子进程边界。
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

from harness.session.store import SessionStore
from harness.sandbox.subprocess_executor import run_npc_subprocess, NpcSubprocessError
from harness.orchestrator import advance

_HASH = re.compile(r"^[0-9a-f]{7,40}$")


def _git_init_repo(repo: Path):
    for c in (["git", "init", "-b", "main"], ["git", "config", "user.email", "t@t"],
              ["git", "config", "user.name", "t"], ["git", "commit", "--allow-empty", "-m", "init"]):
        subprocess.run(c, cwd=repo, check=True, capture_output=True)


_CARD = {
    "task_id": "t-login", "assignee_role": "builder",
    "objective": "实现 login.py", "output_format": "login.py",
    "allowed_tools": ["read", "write", "bash"], "boundaries": ["只用标准库"],
    "verification": [{"type": "machine_verifiable",
                      "command": "python -c \"import login\"",
                      "expected": {"exit_code": 0}}],
}
_SCRIPTED = [{"tool": "write", "params": {"path": "login.py", "content": "V = 1\n"},
              "task_id": "t-login"}]


def _seed_trigger(store: SessionStore) -> int:
    """建 user_command → guide_delegate → guide_assign 链,返回 guide_assign event_id 作 trigger。"""
    uc = store.append_event(agent="user", type="user_command",
                            payload={"text": "做个登录"}, session_id="subp")
    gd = store.append_event(agent="guide", type="guide_delegate",
                            parent_event_id=uc["event_id"], session_id="subp",
                            payload={"task_card": _CARD})
    ga = store.append_event(agent="guide", type="guide_assign",
                            parent_event_id=gd["event_id"], session_id="subp",
                            payload={"task_card_event_id": gd["event_id"],
                                     "assignee_instance": "merchant#1"})
    return ga["event_id"]


def test_subprocess_writes_to_shared_db(tmp_path):
    """INV-1/2: 子进程 builder 的事件落进 parent 同一 SQLite(多写者 WAL)。"""
    repo = tmp_path / "repo"; repo.mkdir(); _git_init_repo(repo)
    db = tmp_path / "session.db"
    store = SessionStore(db, session_id="subp")
    trigger = _seed_trigger(store)

    run_npc_subprocess(
        npc_instance_id="merchant#1", task_card=_CARD,
        db_path=str(db), session_id="subp", guide_assign_event_id=trigger,
        repo_root=str(repo), worktrees_base=str(tmp_path / "worktrees"),
        scripted_actions=_SCRIPTED, timeout=60,
    )

    # parent 的连接读到 child 写的事件(新读快照见已提交数据)
    events = store.query_session()
    types = [e["type"] for e in events if e["agent"] == "merchant#1"]
    assert "tool_intent" in types and "tool_done" in types and "review_request" in types, \
        f"子进程应写 tool_intent/tool_done/review_request,实际 merchant#1 事件: {types}"
    # 产物真实落到 worktree
    assert (tmp_path / "worktrees" / "merchant-1" / "login.py").exists()


def test_subprocess_crash_isolated(tmp_path):
    """INV-3: child 崩溃 → NpcSubprocessError;主进程存活、无 review_request 半产物。"""
    repo = tmp_path / "repo"; repo.mkdir(); _git_init_repo(repo)
    db = tmp_path / "session.db"
    store = SessionStore(db, session_id="subp")
    _seed_trigger(store)

    with pytest.raises(NpcSubprocessError):
        run_npc_subprocess(
            npc_instance_id="merchant#1", task_card=_CARD,
            db_path=str(db), session_id="subp",
            guide_assign_event_id=999999,  # 不存在 → child execute_npc 抛 → 非零退出
            repo_root=str(repo), worktrees_base=str(tmp_path / "worktrees"),
            scripted_actions=_SCRIPTED, timeout=60,
        )

    # 主进程存活:仍能查库;崩溃发生在产物前 → 无 review_request
    events = store.query_session()
    assert not any(e["type"] == "review_request" for e in events), "崩溃前不应有 review_request"


def test_orchestrator_subprocess_parity(tmp_path):
    """INV-4: advance(npc_in_subprocess=True) 单卡 scripted → verified_pass + 真 merge。"""
    class _Stub:
        def complete(self, model, messages, **kwargs):
            system = messages[0]["content"] if messages else ""
            if "裁缝" in system or "Tailor" in system:
                return json.dumps({"verdict": "pass", "notes": "ok"}, ensure_ascii=False)
            return json.dumps({"thinking": "单卡", "tasks": [_CARD]}, ensure_ascii=False)

    repo = tmp_path / "repo"; repo.mkdir(); _git_init_repo(repo)
    store = SessionStore(tmp_path / "session.db", session_id="subp")
    store.append_event(agent="user", type="user_command",
                       payload={"text": "做个登录"}, session_id="subp")

    report = advance(store, llm_client=_Stub(), repo_root=repo,
                     worktrees_base=tmp_path / "worktrees",
                     builder_scripted_actions={"t-login": _SCRIPTED},
                     npc_in_subprocess=True)

    assert report["task_board"]["t-login"]["status"] == "verified_pass"
    events = store.query_session()
    merges = [e for e in events if e["type"] == "merge"]
    assert len(merges) == 1 and merges[0]["payload"]["result"] == "success"
    assert _HASH.match(merges[0]["payload"]["commit"]), "子进程路径下 merge 仍带真实 commit"
    # builder 事件确由子进程写入同一库
    assert any(e["agent"] == "merchant#1" and e["type"] == "tool_done" for e in events)
