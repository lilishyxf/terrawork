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
  think: { text: string; event_id: number } | null;
}
export type TaskStatus =
  | "queued" | "building" | "awaiting_review" | "verifying" | "post_verify"
  | "reviewing" | "merged" | "blocked" | "rejected";
export interface TaskBoardEntry {
  status: TaskStatus;
  builder: string | null;
  last_event_id: number;
}
export interface ViewSnapshot {
  npcs: Record<string, NpcSnapshot>;
  task_board: Record<string, TaskBoardEntry>;
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

// ── 显示用:角色的功能标签(干什么的;纯展示,不影响投影/parity)──────────
// 显示职能而非花名/昵称:如 tailor=代码审查、blaster=验证者。
export const ROLE_LABEL: Record<string, string> = {
  guide: "向导", frontend: "前端开发", backend: "后端开发", database: "数据库",
  desktop_shell: "桌面端", ai_engineer: "AI 工程", rapid_proto: "快速原型",
  tech_writer: "技术文档", mobile: "移动端", merchant: "通用开发",
  blaster: "验证者", tailor: "代码审查", appsec: "安全审查",
};
/** 实例 id(如 tailor#2)→ 功能标签(多实例加序号):"代码审查2";取不到则原样。 */
export function agentName(id: string): string {
  const label = ROLE_LABEL[roleOf(id)];
  if (!label) return id;
  const n = id.includes("#") ? id.split("#")[1] : "";
  return n && n !== "1" ? `${label}${n}` : label;
}

export function project(events: TerraEvent[]): ViewSnapshot {
  const npcs: Record<string, NpcSnapshot> = {};
  const delegateTask: Record<number, string> = {};      // guide_delegate.event_id → task_id
  const builderOfTask: Record<string, string> = {};     // task_id → builder 实例
  const taskBoard: Record<string, TaskBoardEntry> = {};
  let lastMerge: ViewSnapshot["last_merge"] = null;
  let cursor: number | null = null;

  const present = (id: string) => {
    if (!npcs[id]) {
      npcs[id] = { kind: kindOf(id), sprite_key: spriteOf(id),
                   state: "idle", zone: zoneOf(id, "idle"),
                   task_id: null, think: null };
    }
  };
  const touchTask = (tid: string | null | undefined, eid: number, status: TaskStatus,
                     builder?: string) => {
    if (!tid) return;
    const slot = taskBoard[tid] ?? { status, builder: builder ?? null, last_event_id: eid };
    slot.status = status;
    slot.last_event_id = eid;
    if (builder) slot.builder = builder;
    taskBoard[tid] = slot;
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
    const eid = e.event_id;
    cursor = eid ?? cursor;
    const t = e.type;
    const p = (e.payload ?? {}) as Record<string, unknown>;
    const agent = e.agent;
    const tid = (p.task_id as string | undefined) ?? null;

    if (t === "user_command") setState("guide", "decomposing");
    else if (t === "guide_think") {
      setState("guide", "thinking");
      npcs["guide"].think = { text: (p.text as string) ?? "", event_id: eid };
    }
    else if (t === "guide_delegate") {
      const tc = p.task_card as { task_id: string } | undefined;
      if (tc) { delegateTask[eid] = tc.task_id; touchTask(tc.task_id, eid, "queued"); }
    }
    else if (t === "guide_assign") {
      const inst = p.assignee_instance as string;
      const dtid = delegateTask[p.task_card_event_id as number] ?? null;
      const k = kindOf(inst);
      if (k === "builder") {
        // 同一 builder 实例再次派 = 返工(首次出现 → working)
        setState(inst, inst in npcs ? "rework" : "working", dtid);
        if (dtid !== null) builderOfTask[dtid] = inst;
        touchTask(dtid, eid, "building", inst);
      } else if (k === "verifier") { setState(inst, "verifying", dtid); touchTask(dtid, eid, "verifying"); }
      else if (k === "reviewer") { setState(inst, "reviewing", dtid); touchTask(dtid, eid, "reviewing"); }
      setState("guide", "idle"); // 派完回大堂
    }
    else if (t === "npc_think") {
      setState(agent, "thinking");
      present(agent); npcs[agent].think = { text: (p.text as string) ?? "", event_id: eid };
    }
    else if (t === "tool_intent" || t === "tool_done") {
      setState(agent, "working"); touchTask(tid, eid, "building");
    }
    else if (t === "review_request") {
      setState(agent, "awaiting_review", tid); touchTask(tid, eid, "awaiting_review");
    }
    else if (t === "verify_run") {
      setState(agent, "idle"); touchTask(tid, eid, "post_verify");
    }
    else if (t === "review_verdict") {
      setState(agent, "idle");
      touchTask(tid, eid, p.verdict === "reject" ? "rejected" : "reviewing");
    }
    else if (t === "merge") {
      const mtid = p.task_id as string;
      const bi = builderOfTask[mtid];
      if (bi) setState(bi, "idle", mtid);
      if (p.result === "success") {
        lastMerge = { task_id: mtid, event_id: eid };
        touchTask(mtid, eid, "merged");
      } else touchTask(mtid, eid, "blocked"); // conflict
    }
    else if (t === "hitl_request") {
      setState("guide", "hitl", tid); touchTask(tid, eid, "blocked");
    }
    else if (t === "error" && agent) setState(agent, "error");
  }

  return { npcs, task_board: taskBoard, last_merge: lastMerge, cursor };
}
