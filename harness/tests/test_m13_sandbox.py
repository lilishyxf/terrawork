"""M1.3 contract tests — sandbox + single NPC execution.

M1.3-1: 仅数据层自洽 (data-scope invariants on reference_output).
M1.3-3: 端到端(mock executor / worktree)将启用 runtime-scope invariants.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

from harness.session.schema import validate_event
from harness.session.store import SessionStore
from harness.sandbox.executor import execute_npc

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    with open(FIXTURE_DIR / name, encoding="utf-8") as f:
        return json.load(f)


# ---- INV-2 ~ INV-7：data-scope 校验函数（对照 m13 reference_output） ----

def check_inv_2_tool_events_paired_via_parent(events: list[dict]) -> None:
    """每个 tool_intent 恰好一个 tool_done 配对：parent 链 + 同 agent + 同 tool。严格 1:1。"""
    intents = [e for e in events if e["type"] == "tool_intent"]
    dones = [e for e in events if e["type"] == "tool_done"]
    for it in intents:
        matched = [d for d in dones if d["parent_event_id"] == it["event_id"]]
        assert len(matched) == 1, \
            f"INV-2: tool_intent#{it['event_id']} 应恰好 1 个配对 tool_done，实际 {len(matched)}"
        d = matched[0]
        assert d["agent"] == it["agent"], \
            f"INV-2: tool_done#{d['event_id']} agent 与配对 intent 不一致"
        assert d["payload"]["tool"] == it["payload"]["tool"], \
            f"INV-2: tool_done#{d['event_id']} tool 与配对 intent 不一致"
    for d in dones:
        parents = [it for it in intents if it["event_id"] == d["parent_event_id"]]
        assert len(parents) == 1, \
            f"INV-2: tool_done#{d['event_id']} 应恰好链回 1 个 tool_intent（无孤儿/无 N:1）"
    assert len(intents) == len(dones), \
        f"INV-2: tool_intent({len(intents)}) 与 tool_done({len(dones)}) 数量应相等（严格 1:1）"


def check_inv_3_parent_chain_strictly_backward(events: list[dict], trigger: dict) -> None:
    """parent_event_id 正整数、链回 trigger 或先前事件、严格 < 自身 event_id（防环）。"""
    valid_ids = {trigger["event_id"]} | {e["event_id"] for e in events}
    for e in events:
        pid = e.get("parent_event_id")
        assert isinstance(pid, int) and pid >= 1, \
            f"INV-3: event#{e['event_id']} parent_event_id 必须为正整数"
        assert pid in valid_ids, \
            f"INV-3: event#{e['event_id']} parent_event_id={pid} 未链回 trigger 或先前事件"
        assert pid < e["event_id"], \
            f"INV-3: event#{e['event_id']} parent_event_id={pid} 必须指向更早事件（防环）"


def check_inv_4_agent_field_consistent(events: list[dict], trigger: dict) -> None:
    """tool_intent / tool_done / review_request 的 agent 必须 == assignee_instance。"""
    inst = trigger["payload"]["assignee_instance"]
    for e in events:
        if e["type"] in ("tool_intent", "tool_done", "review_request"):
            assert e["agent"] == inst, \
                f"INV-4: event#{e['event_id']} agent={e['agent']!r} 应为实例 {inst!r}"


def check_inv_5_tool_within_whitelist(events: list[dict], prior: list[dict]) -> None:
    """tool_intent.payload.tool 必须在 prior_context 任务卡的 allowed_tools 内。"""
    allowed: set[str] = set()
    for e in prior:
        if e["type"] == "guide_delegate":
            allowed |= set(e["payload"]["task_card"]["allowed_tools"])
    assert allowed, "INV-5: prior_context 未找到任何 task_card.allowed_tools"
    for e in events:
        if e["type"] == "tool_intent":
            tool = e["payload"]["tool"]
            assert tool in allowed, \
                f"INV-5: event#{e['event_id']} tool {tool!r} 不在白名单 {sorted(allowed)}"


def check_inv_6_all_schemas_pass(events: list[dict]) -> None:
    """所有事件过 events.schema.json；tool_done 含 status；review_request 含三字段。"""
    for e in events:
        validate_event(e)  # raises SchemaError if invalid
        if e["type"] == "tool_done":
            assert e["payload"].get("status") in ("ok", "error"), \
                f"INV-6: tool_done#{e['event_id']} status 必须 ∈ {{ok, error}}"
        if e["type"] == "review_request":
            for field in ("task_id", "reviewer", "artifact"):
                assert field in e["payload"], \
                    f"INV-6: review_request#{e['event_id']} 缺字段 {field}"


def check_inv_7_completion_signal(events: list[dict], trigger: dict, prior: list[dict]) -> None:
    """恰好 1 个 review_request，parent 链回 trigger，task_id == 任务卡 task_id。"""
    reqs = [e for e in events if e["type"] == "review_request"]
    assert len(reqs) == 1, f"INV-7: review_request 应恰好 1 个，实际 {len(reqs)}"
    req = reqs[0]
    assert req["parent_event_id"] == trigger["event_id"], \
        f"INV-7: review_request parent={req['parent_event_id']} 应链回 trigger#{trigger['event_id']}"
    tc_eid = trigger["payload"]["task_card_event_id"]
    task_card = next(
        e["payload"]["task_card"] for e in prior
        if e["type"] == "guide_delegate" and e["event_id"] == tc_eid
    )
    assert req["payload"]["task_id"] == task_card["task_id"], \
        f"INV-7: review_request task_id={req['payload']['task_id']!r} 应为 {task_card['task_id']!r}"


# ---- M1.3-1 自洽测试：纯数据层 ----

def test_reference_output_passes_data_scope_invariants():
    """M1.3-1 自洽 sanity check：reference_output 通过所有 scope=='data' 的 INV。

    通过此测试不代表 sandbox 实跑能产出同样事件流；只代表 fixture 内部一致。
    """
    fx = load_fixture("m13_merchant_login_impl.json")
    events = fx["reference_output"]
    trigger = fx["trigger"]
    prior = fx["prior_context"]

    data_invs = [inv for inv in fx["invariants"] if inv["scope"] == "data"]
    assert len(data_invs) == 6, f"M1.3-1 期望 6 条 data-scope INV，实际 {len(data_invs)}"

    check_inv_2_tool_events_paired_via_parent(events)
    check_inv_3_parent_chain_strictly_backward(events, trigger)
    check_inv_4_agent_field_consistent(events, trigger)
    check_inv_5_tool_within_whitelist(events, prior)
    check_inv_6_all_schemas_pass(events)
    check_inv_7_completion_signal(events, trigger, prior)


# ---- M1.3-3 端到端测试：scripted executor ----

def _make_repo_with_initial_commit(repo_path: Path) -> None:
    """tmp_path 下建一个能 git worktree add 的最小仓库。"""
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo_path, check=True, capture_output=True)
    test_dir = repo_path / "__tests__"
    test_dir.mkdir(parents=True)
    (test_dir / "login.test.ts").write_text(
        "describe('login', () => { it('passes', () => {}); });",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, check=True, capture_output=True)


def _insert_event(store, ev: dict) -> dict:
    """按 SessionStore 真实 API(keyword)灌入一个完整 fixture 事件。"""
    return store.append_event(
        agent=ev["agent"],
        type=ev["type"],
        payload=ev["payload"],
        parent_event_id=ev["parent_event_id"],
        session_id=ev["session_id"],
        ts=ev["ts"],
    )


def _extract_scripted_actions(fixture: dict) -> list[dict]:
    """从 fixture.reference_output 提取 tool_intent,组装 scripted_actions(write 补 dummy content)。"""
    scripted = []
    for e in fixture["reference_output"]:
        if e["type"] != "tool_intent":
            continue
        tool = e["payload"]["tool"]
        params = dict(e["payload"]["params"])
        if tool == "write":
            params["content"] = "// generated for M1.3-3 end-to-end test\nexport async function login() {}\n"
        action = {"tool": tool, "params": params}
        if "task_id" in e["payload"]:
            action["task_id"] = e["payload"]["task_id"]
        scripted.append(action)
    return scripted


def test_executor_scripted_satisfies_all_invariants(tmp_path):
    """M1.3-3 端到端:scripted executor 跑完产出事件流,满足 INV-1~7(含 runtime)。"""
    # 1. 临时 git repo
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_repo_with_initial_commit(repo)

    # 2. 临时 session.db
    store = SessionStore(tmp_path / "session.db")

    # 3. 灌入 prior_context + trigger(按真实 keyword API)
    fx = load_fixture("m13_merchant_login_impl.json")
    for ev in fx["prior_context"]:
        _insert_event(store, ev)
    _insert_event(store, fx["trigger"])

    # 4. 跑 executor
    task_card = fx["prior_context"][2]["payload"]["task_card"]
    scripted = _extract_scripted_actions(fx)
    execute_npc(
        npc_instance_id="merchant#1",
        task_card=task_card,
        session_store=store,
        guide_assign_event_id=fx["trigger"]["event_id"],
        repo_root=repo,
        worktrees_base=tmp_path / "worktrees",
        scripted_actions=scripted,
    )

    # 5. 取出执行期间产生的新事件(event_id > trigger)
    all_events = store.query_session()
    new_events = [e for e in all_events if e["event_id"] > fx["trigger"]["event_id"]]
    assert len(new_events) >= 7  # 3 intent + 3 done + 1 review_request

    # 6. INV-1 runtime:worktree 真创建了
    wt = tmp_path / "worktrees" / "merchant-1"
    assert wt.exists(), "INV-1: worktree 未创建"
    assert (wt / ".git").exists(), "INV-1: worktree 缺 .git 标识"

    # 7. INV-2~7 复用既有 check 函数
    trigger = fx["trigger"]
    check_inv_2_tool_events_paired_via_parent(new_events)
    check_inv_3_parent_chain_strictly_backward(new_events, trigger)
    check_inv_4_agent_field_consistent(new_events, trigger)
    check_inv_5_tool_within_whitelist(new_events, fx["prior_context"])
    check_inv_6_all_schemas_pass(new_events)
    check_inv_7_completion_signal(new_events, trigger, fx["prior_context"])


# ---- M1.3-4 端到端 live 测试：真实 DeepSeek tool calling + Python 自验任务 ----

def _make_empty_python_repo(repo_path: Path) -> None:
    """Live test 用:干净 Python repo,merchant 从零写。"""
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo_path, check=True, capture_output=True)
    (repo_path / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, check=True, capture_output=True)


LIVE_TASK_CARD = {
    "task_id": "t-py-login-impl",
    "assignee_role": "builder",
    "objective": (
        "实现 login.py:暴露 login(username: str, password: str) -> dict 函数。"
        "用 Python 标准库(不引入第三方依赖)。"
        "返回字典 {'ok': True/False, 'user': username}。"
        "凭据规则(仅本任务用,非产品级):"
        "用户 'alice' 合法密码 'secret';用户 'bob' 合法密码 'hunter2';"
        "其他用户名或错误密码返回 {'ok': False}。"
        "用 hashlib 做密码哈希比较,不要明文存密码常量。"
    ),
    "output_format": "单文件 login.py,放在 worktree 根目录。",
    "allowed_tools": ["read", "write", "bash"],
    "boundaries": [
        "只能用 Python 标准库(hashlib / hmac / typing 等),禁用第三方包",
        "login.py 必须在 worktree 根目录,文件名严格为 login.py",
        "不要写测试文件;实现完成后用 bash 跑 python -c '...' 自验",
        "不要修改 .git/ 任何内容",
    ],
    "verification": [
        {
            "type": "machine_verifiable",
            "command": (
                "python -c \"from login import login; "
                "assert login('alice','secret')['ok']==True; "
                "assert login('alice','wrong')['ok']==False; "
                "assert login('bob','hunter2')['ok']==True; "
                "assert login('unknown','any')['ok']==False; "
                "print('OK')\""
            ),
            "expected": {"exit_code": 0},
        }
    ],
}


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY not set; skipping live LLM test",
)
def test_executor_live_deepseek_satisfies_invariants(tmp_path, monkeypatch):
    """M1.3-4 端到端 live:真实 DeepSeek tool calling + Python 自验任务。

    收敛机制:boundaries 明示"实现完用 bash 跑 python -c 自验",merchant 写完
    login.py 后跑 verification 命令拿到 exit_code=0,自然不再调工具 → 完成信号。

    LLM 轨迹仍不可预测(可能勘探目录 2-3 次后再写),只校验结构性 INV-1~7。

    prior chain 用 schema 合法事件(guide_delegate + guide_assign)构造——
    check_inv_5/7 实际读 prior 里的 guide_delegate.payload.task_card,不依赖
    world_state 之类(那不是 events.schema 的事件类型)。
    """
    monkeypatch.setenv("TERRA_LLM_MODE", "real")

    # 1. 干净 Python repo
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_empty_python_repo(repo)

    # 2. 临时 session.db
    store = SessionStore(tmp_path / "session.db")

    # 3. 构造 schema 合法的 prior chain:guide_delegate(挂 LIVE_TASK_CARD) → guide_assign
    session_id = "live-test-session-001"
    delegate_ev = store.append_event(
        agent="guide",
        type="guide_delegate",
        payload={"task_card": LIVE_TASK_CARD},
        session_id=session_id,
    )
    trigger_ev = store.append_event(
        agent="guide",
        type="guide_assign",
        payload={
            "task_card_event_id": delegate_ev["event_id"],
            "assignee_instance": "merchant#1",
        },
        parent_event_id=delegate_ev["event_id"],
        session_id=session_id,
    )
    trigger_eid = trigger_ev["event_id"]

    # 4. 跑 executor,scripted_actions=None → LLM 真实路径
    execute_npc(
        npc_instance_id="merchant#1",
        task_card=LIVE_TASK_CARD,
        session_store=store,
        guide_assign_event_id=trigger_eid,
        repo_root=repo,
        worktrees_base=tmp_path / "worktrees",
        scripted_actions=None,
        max_iterations=20,
    )

    # 5. 取出 merchant 产出的事件
    all_events = store.query_session(session_id=session_id)
    new_events = [e for e in all_events if e["event_id"] > trigger_eid]
    prior_events = [e for e in all_events if e["event_id"] <= trigger_eid]

    types = [e["type"] for e in new_events]
    assert "tool_intent" in types, f"INV: 无 tool_intent. 事件类型: {types}"
    assert "tool_done" in types, f"INV: 无 tool_done. 事件类型: {types}"
    assert types[-1] == "review_request", f"INV-7: 末事件应为 review_request, 实际: {types}"

    # INV-1 runtime: worktree 真创建
    wt = tmp_path / "worktrees" / "merchant-1"
    assert wt.exists() and (wt / ".git").exists(), "INV-1: worktree 未创建或缺 .git"

    # INV-2~7（复用既有 check 函数；prior 传 schema 合法的 delegate+assign 两条）
    check_inv_2_tool_events_paired_via_parent(new_events)
    check_inv_3_parent_chain_strictly_backward(new_events, trigger_ev)
    check_inv_4_agent_field_consistent(new_events, trigger_ev)
    check_inv_5_tool_within_whitelist(new_events, prior_events)
    check_inv_6_all_schemas_pass(new_events)
    check_inv_7_completion_signal(new_events, trigger_ev, prior_events)
