"""M3-1 View 动画投影(ADR-020)。

纯函数 reducer:顺序消费 Session 事件流,产出每个 NPC 的当前动画状态快照。
等价 Catch-up 快进(replay 到游标即得当前状态,ADR-001)。无副作用、不调 LLM。
状态/区/sprite-key 见 docs/contracts/animation_protocol.json + ADR-020。
"""
import json
import re
from pathlib import Path

_PROTO_FILE = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "animation_protocol.json"
_PROTO = json.loads(_PROTO_FILE.read_text(encoding="utf-8"))
_STATES = set(_PROTO["states"])

_INSTANCE_RE = re.compile(r"^([a-z_][a-z0-9_]*)#[0-9]+$")
_VERIFIER_ROLE = "blaster"
_REVIEWER_ROLES = {"tailor", "appsec"}

# 固定班子(prompt-driven + 机械验证位):恒存在于小镇,缺省 idle 留守 home zone
_FIXED = ("guide", "blaster#1", "tailor#1", "appsec#1")


def _role_of(npc_id: str) -> str:
    if npc_id == "guide":
        return "guide"
    m = _INSTANCE_RE.match(npc_id)
    return m.group(1) if m else npc_id


def _kind(npc_id: str) -> str:
    role = _role_of(npc_id)
    if role == "guide":
        return "guide"
    if role == _VERIFIER_ROLE:
        return "verifier"
    if role in _REVIEWER_ROLES:
        return "reviewer"
    return "builder"


def _sprite_key(npc_id: str) -> str:
    return _role_of(npc_id)


def _zone(npc_id: str, state: str) -> str:
    """state(+ npc 类型)→ 空间区(ADR-020)。idle/thinking 随类型不同;error 由调用方保留原区。"""
    kind = _kind(npc_id)
    if state == "hitl":
        return "at_glass"
    if kind == "guide":
        return "lobby"  # 向导基本驻大堂(decomposing/thinking/idle/arbitrating)
    if state == "idle":
        return {"verifier": "verify_room", "reviewer": "review_room"}.get(kind, "yard")
    if state == "thinking":
        return "workshop" if kind == "builder" else \
            {"verifier": "verify_room", "reviewer": "review_room"}.get(kind, "lobby")
    return {  # 其余直接由 state 决定(builder 主体)
        "working": "workshop", "rework": "workshop", "decomposing": "lobby",
        "awaiting_review": "review_door", "verifying": "verify_room", "reviewing": "review_room",
    }[state]


def project(events: list[dict]) -> dict:
    """事件流 → View 动画快照。

    Returns:
        {
          "npcs": {npc_id: {"kind","sprite_key","state","zone","task_id"}},
          "last_merge": {"task_id","event_id"} | None,
          "cursor": last event_id | None,
        }
    """
    npcs: dict = {}
    delegate_task: dict = {}      # guide_delegate.event_id -> task_id
    builder_of_task: dict = {}    # task_id -> builder 实例
    last_merge = None
    cursor = None

    def present(npc_id: str):
        if npc_id not in npcs:
            npcs[npc_id] = {"kind": _kind(npc_id), "sprite_key": _sprite_key(npc_id),
                            "state": "idle", "zone": _zone(npc_id, "idle"), "task_id": None}

    def set_state(npc_id: str, state: str, task_id=None):
        present(npc_id)
        cur = npcs[npc_id]
        zone = cur["zone"] if state == "error" else _zone(npc_id, state)  # error 保留原区
        cur.update(state=state, zone=zone)
        if task_id is not None:
            cur["task_id"] = task_id

    for n in _FIXED:           # 固定班子恒在场
        present(n)

    for e in events:
        cursor = e.get("event_id", cursor)
        t = e["type"]
        p = e.get("payload", {})
        agent = e.get("agent")

        if t == "user_command":
            set_state("guide", "decomposing")
        elif t == "guide_think":
            set_state("guide", "thinking")
        elif t == "guide_delegate":
            delegate_task[e["event_id"]] = p["task_card"]["task_id"]
        elif t == "guide_assign":
            inst = p["assignee_instance"]
            tid = delegate_task.get(p.get("task_card_event_id"))
            kind = _kind(inst)
            if kind == "builder":
                # 同一 builder 实例再次派发 = 返工(首次出现 → working)
                set_state(inst, "rework" if inst in npcs else "working", tid)
            elif kind == "verifier":
                set_state(inst, "verifying", tid)
            elif kind == "reviewer":
                set_state(inst, "reviewing", tid)
            set_state("guide", "idle")  # 派完回大堂
        elif t == "npc_think":
            set_state(agent, "thinking")
        elif t in ("tool_intent", "tool_done"):
            set_state(agent, "working")
        elif t == "review_request":
            set_state(agent, "awaiting_review", p.get("task_id"))
        elif t == "verify_run":
            set_state(agent, "idle")            # 爆破工验完留守
        elif t == "review_verdict":
            set_state(agent, "idle")            # reviewer 审完留守
        elif t == "merge":
            tid = p.get("task_id")
            bi = builder_of_task.get(tid)
            if bi:
                set_state(bi, "idle", tid)      # 该卡 builder 回院子
            if p.get("result") == "success":
                last_merge = {"task_id": tid, "event_id": e["event_id"]}
        elif t == "hitl_request":
            set_state("guide", "hitl", p.get("task_id"))
        elif t == "error":
            if agent:
                set_state(agent, "error")

        # 维护 task -> builder 实例映射(供 merge 时回收)
        if t == "guide_assign":
            inst = p["assignee_instance"]
            if _kind(inst) == "builder":
                tid = delegate_task.get(p.get("task_card_event_id"))
                if tid is not None:
                    builder_of_task[tid] = inst

    return {"npcs": npcs, "last_merge": last_merge, "cursor": cursor}
