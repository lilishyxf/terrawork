// M3-4 View 动画投影(TS 端口,ADR-020)。Python harness/view/projection.py 的镜像。
// 一致性由 src/game/protocol/projection.test.ts 锁(对同一 fixture parity)。
import type { TerraEvent } from "../../ipc/subscribe";

export type NpcKind = "guide" | "verifier" | "reviewer" | "builder";
export type NpcState =
  | "idle" | "decomposing" | "thinking" | "working" | "rework"
  | "awaiting_review" | "verifying" | "reviewing" | "hitl" | "error";
export type Zone =
  | "yard" | "lobby" | "workshop" | "review_door"
  | "verify_room" | "review_room" | "at_glass";

export interface NpcSnapshot {
  kind: NpcKind;
  sprite_key: string;
  state: NpcState;
  zone: Zone;
  task_id: string | null;
}
export interface ViewSnapshot {
  npcs: Record<string, NpcSnapshot>;
  last_merge: { task_id: string; event_id: number } | null;
  cursor: number | null;
}

const INSTANCE_RE = /^([a-z_][a-z0-9_]*)#[0-9]+$/;
const VERIFIER_ROLE = "blaster";
const REVIEWER_ROLES = new Set(["tailor", "appsec"]);
const FIXED = ["guide", "blaster#1", "tailor#1", "appsec#1"];

function roleOf(npcId: string): string {
  if (npcId === "guide") return "guide";
  const m = INSTANCE_RE.exec(npcId);
  return m ? m[1] : npcId;
}
function kindOf(npcId: string): NpcKind {
  const r = roleOf(npcId);
  if (r === "guide") return "guide";
  if (r === VERIFIER_ROLE) return "verifier";
  if (REVIEWER_ROLES.has(r)) return "reviewer";
  return "builder";
}
function spriteOf(npcId: string): string { return roleOf(npcId); }

function zoneOf(npcId: string, state: NpcState): Zone {
  const kind = kindOf(npcId);
  if (state === "hitl") return "at_glass";
  if (kind === "guide") return "lobby";
  if (state === "idle") {
    return kind === "verifier" ? "verify_room" : kind === "reviewer" ? "review_room" : "yard";
  }
  if (state === "thinking") {
    if (kind === "builder") return "workshop";
    return kind === "verifier" ? "verify_room" : kind === "reviewer" ? "review_room" : "lobby";
  }
  // 其余 state 直接由 state 决定
  const map: Partial<Record<NpcState, Zone>> = {
    working: "workshop", rework: "workshop", decomposing: "lobby",
    awaiting_review: "review_door", verifying: "verify_room", reviewing: "review_room",
  };
  return map[state]!;
}

export function project(events: TerraEvent[]): ViewSnapshot {
  const npcs: Record<string, NpcSnapshot> = {};
  const delegateTask: Record<number, string> = {};      // guide_delegate.event_id → task_id
  const builderOfTask: Record<string, string> = {};     // task_id → builder 实例
  let lastMerge: ViewSnapshot["last_merge"] = null;
  let cursor: number | null = null;

  const present = (id: string) => {
    if (!npcs[id]) {
      npcs[id] = { kind: kindOf(id), sprite_key: spriteOf(id),
                   state: "idle", zone: zoneOf(id, "idle"), task_id: null };
    }
  };
  const setState = (id: string, state: NpcState, taskId: string | null = null) => {
    present(id);
    const cur = npcs[id];
    cur.zone = state === "error" ? cur.zone : zoneOf(id, state); // error 保留原区
    cur.state = state;
    if (taskId !== null) cur.task_id = taskId;
  };

  for (const n of FIXED) present(n); // 固定班子恒在场

  for (const e of events) {
    cursor = e.event_id ?? cursor;
    const t = e.type;
    const p = (e.payload ?? {}) as Record<string, unknown>;
    const agent = e.agent;

    if (t === "user_command") setState("guide", "decomposing");
    else if (t === "guide_think") setState("guide", "thinking");
    else if (t === "guide_delegate") {
      const tc = p.task_card as { task_id: string } | undefined;
      if (tc) delegateTask[e.event_id] = tc.task_id;
    }
    else if (t === "guide_assign") {
      const inst = p.assignee_instance as string;
      const tid = delegateTask[p.task_card_event_id as number] ?? null;
      const k = kindOf(inst);
      if (k === "builder") {
        // 同一 builder 实例再次派 = 返工(首次出现 → working)
        setState(inst, inst in npcs ? "rework" : "working", tid);
        if (tid !== null) builderOfTask[tid] = inst;
      } else if (k === "verifier") setState(inst, "verifying", tid);
      else if (k === "reviewer") setState(inst, "reviewing", tid);
      setState("guide", "idle"); // 派完回大堂
    }
    else if (t === "npc_think") setState(agent, "thinking");
    else if (t === "tool_intent" || t === "tool_done") setState(agent, "working");
    else if (t === "review_request") setState(agent, "awaiting_review", (p.task_id as string) ?? null);
    else if (t === "verify_run") setState(agent, "idle");      // 爆破工验完留守
    else if (t === "review_verdict") setState(agent, "idle");  // reviewer 审完留守
    else if (t === "merge") {
      const tid = p.task_id as string;
      const bi = builderOfTask[tid];
      if (bi) setState(bi, "idle", tid);  // 该卡 builder 回院子
      if (p.result === "success") lastMerge = { task_id: tid, event_id: e.event_id };
    }
    else if (t === "hitl_request") setState("guide", "hitl", (p.task_id as string) ?? null);
    else if (t === "error" && agent) setState(agent, "error");
  }

  return { npcs, last_merge: lastMerge, cursor };
}
