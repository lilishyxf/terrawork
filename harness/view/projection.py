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


_TASK_STATUS_FROM_EVENT = {
    # 该 task 最末一条相关事件 → task_board.status(M3-5 task 面板用)
    "guide_delegate":  "queued",
    "guide_assign":    "building",        # 派 builder/verifier/reviewer 都先记 building,后续事件会覆盖
    "tool_intent":     "building",
    "tool_done":       "building",
    "npc_think":       "building",
    "review_request":  "awaiting_review",
    "verify_run":      "post_verify",
    "review_verdict":  "reviewing",       # 默认 pass(等下一位审查);reject 在下方覆盖
    "hitl_request":    "blocked",
    "merge":           "merged",          # success 在下方覆盖到 merged,conflict 留 blocked
}


def project(events: list[dict]) -> dict:
    """事件流 → View 动画快照。

    Returns:
        {
          "npcs": {npc_id: {"kind","sprite_key","state","zone","task_id","think"}},
          "task_board": {task_id: {"status","builder","last_event_id"}},
          "last_merge": {"task_id","event_id"} | None,
          "cursor": last event_id | None,
        }
    `npc.think`:该 NPC 最近一条 npc_think/guide_think 的 {text,event_id};无则缺省。
    """
    npcs: dict = {}
    delegate_task: dict = {}      # guide_delegate.event_id -> task_id
    builder_of_task: dict = {}    # task_id -> builder 实例
    task_board: dict = {}         # task_id -> {status, builder, last_event_id}
    last_merge = None
    cursor = None

    def present(npc_id: str):
        if npc_id not in npcs:
            npcs[npc_id] = {"kind": _kind(npc_id), "sprite_key": _sprite_key(npc_id),
                            "state": "idle", "zone": _zone(npc_id, "idle"),
                            "task_id": None, "think": None}

    def set_state(npc_id: str, state: str, task_id=None):
        present(npc_id)
        cur = npcs[npc_id]
        zone = cur["zone"] if state == "error" else _zone(npc_id, state)  # error 保留原区
        cur.update(state=state, zone=zone)
        if task_id is not None:
            cur["task_id"] = task_id

    def _touch_task(tid, eid, status):
        """记录 task_board 最末事件 → status(M3-5)。"""
        if tid is None:
            return
        slot = task_board.setdefault(tid, {"status": status, "builder": None, "last_event_id": eid})
        slot["status"] = status
        slot["last_event_id"] = eid

    for n in _FIXED:           # 固定班子恒在场
        present(n)

    for e in events:
        eid = e.get("event_id")
        cursor = eid if eid is not None else cursor
        t = e["type"]
        p = e.get("payload", {})
        agent = e.get("agent")
        tid = p.get("task_id") if isinstance(p, dict) else None

        if t == "user_command":
            set_state("guide", "decomposing")
        elif t == "guide_think":
            set_state("guide", "thinking")
            present("guide"); npcs["guide"]["think"] = {"text": p.get("text", ""), "event_id": eid}
        elif t == "guide_delegate":
            tid = p["task_card"]["task_id"]
            delegate_task[e["event_id"]] = tid
            _touch_task(tid, eid, "queued")
        elif t == "guide_assign":
            inst = p["assignee_instance"]
            tid = delegate_task.get(p.get("task_card_event_id"))
            kind = _kind(inst)
            if kind == "builder":
                # 同一 builder 实例再次派发 = 返工(首次出现 → working)
                set_state(inst, "rework" if inst in npcs else "working", tid)
                if tid is not None:
                    builder_of_task[tid] = inst
                    task_board.setdefault(tid, {"status": "building", "builder": inst,
                                                "last_event_id": eid})["builder"] = inst
                _touch_task(tid, eid, "building")
            elif kind == "verifier":
                set_state(inst, "verifying", tid)
                _touch_task(tid, eid, "verifying")
            elif kind == "reviewer":
                set_state(inst, "reviewing", tid)
                _touch_task(tid, eid, "reviewing")
            set_state("guide", "idle")  # 派完回大堂
        elif t == "npc_think":
            set_state(agent, "thinking")
            present(agent); npcs[agent]["think"] = {"text": p.get("text", ""), "event_id": eid}
        elif t in ("tool_intent", "tool_done"):
            set_state(agent, "working")
            _touch_task(tid, eid, "building")
        elif t == "review_request":
            set_state(agent, "awaiting_review", tid)
            _touch_task(tid, eid, "awaiting_review")
        elif t == "verify_run":
            set_state(agent, "idle")            # 爆破工验完留守
            _touch_task(tid, eid, "post_verify")
        elif t == "review_verdict":
            set_state(agent, "idle")            # reviewer 审完留守
            _touch_task(tid, eid,
                       "rejected" if p.get("verdict") == "reject" else "reviewing")
        elif t == "merge":
            mtid = p.get("task_id")
            bi = builder_of_task.get(mtid)
            if bi:
                set_state(bi, "idle", mtid)     # 该卡 builder 回院子
            if p.get("result") == "success":
                last_merge = {"task_id": mtid, "event_id": e["event_id"]}
                _touch_task(mtid, eid, "merged")
            else:
                _touch_task(mtid, eid, "blocked")  # conflict
        elif t == "hitl_request":
            set_state("guide", "hitl", tid)
            _touch_task(tid, eid, "blocked")
        elif t == "error":
            if agent:
                set_state(agent, "error")

    return {"npcs": npcs, "task_board": task_board,
            "last_merge": last_merge, "cursor": cursor}
