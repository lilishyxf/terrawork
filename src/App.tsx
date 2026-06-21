// M3-5 完整 View:订阅事件 → 投影 → Phaser 小镇 + 悬停看 think + 任务板侧栏。
import { useEffect, useMemo, useRef, useState } from "react";
import { subscribe, postCommand, postHitl, type TerraEvent, type Phase } from "./ipc/subscribe";
import { project, type ViewSnapshot, type NpcSnapshot, type TaskStatus } from "./game/protocol/projection";
import { createTown, type TownScene } from "./game/town";
import type Phaser from "phaser";

const DEFAULT_BASE = "http://127.0.0.1:8000";

// 模型选择(全局覆盖所有 NPC)。空值=各角色用 roles/*.md 默认。
// 只列支持 function-calling + JSON 输出的模型(builder 要工具调用);需对应 .env 里的 key。
const MODELS: { label: string; value: string }[] = [
  { label: "按角色默认", value: "" },
  { label: "DeepSeek Chat", value: "deepseek/deepseek-chat" },
  { label: "GPT-4o", value: "openai/gpt-4o" },
  { label: "GPT-4o mini", value: "openai/gpt-4o-mini" },
  { label: "Claude 3.5 Sonnet", value: "anthropic/claude-3-5-sonnet-20241022" },
];

// sprite_key(= 角色名) → 中文职位(取自 roles/*.md 的 display_name)
const ROLE_TITLE: Record<string, string> = {
  guide: "向导", blaster: "爆破专家", tailor: "裁缝", appsec: "应用安全工程师",
  frontend: "前端开发者", backend: "后端架构师", database: "数据库优化器",
  desktop_shell: "桌面壳工程师", ai_engineer: "AI 工程师", rapid_proto: "快速原型机",
  tech_writer: "技术写作", mobile: "移动应用构建器", merchant: "商人",
};
// 实例 id(如 tailor#1)→ "裁缝 #1";取不到职位则原样
function roleTitle(id: string, spriteKey: string): string {
  const t = ROLE_TITLE[spriteKey];
  const hash = id.includes("#") ? " #" + id.split("#")[1] : "";
  return t ? t + hash : id;
}

// task_board 列表的状态颜色(与 Phaser 色板呼应)
const STATUS_COLOR: Record<TaskStatus, string> = {
  queued: "#bdbdbd", building: "#2e86c1", awaiting_review: "#e67e22",
  verifying: "#9b59b6", post_verify: "#7e57c2", reviewing: "#16a085",
  merged: "#4a8c4a", blocked: "#e74c3c", rejected: "#c0392b",
};

// 待回应的 HITL = 没有 hitl_response 指向它的最新 hitl_request
function computeOpenHitl(events: TerraEvent[]) {
  const answered = new Set(
    events.filter((e) => e.type === "hitl_response").map((e) => e.parent_event_id),
  );
  const open = events.filter((e) => e.type === "hitl_request" && !answered.has(e.event_id));
  const last = open[open.length - 1];
  return last
    ? { event_id: last.event_id, question: String(last.payload.question ?? ""),
        task_id: last.payload.task_id as string | undefined }
    : null;
}

// 向导对非任务输入的直接回复(ADR-023)= 父为 user_command、且没有 guide_delegate 子节点的最新 guide_think
function computeGuideReply(events: TerraEvent[]) {
  const thinkWithDelegate = new Set(
    events.filter((e) => e.type === "guide_delegate").map((e) => e.parent_event_id),
  );
  const cmdIds = new Set(
    events.filter((e) => e.type === "user_command").map((e) => e.event_id),
  );
  const replies = events.filter(
    (e) => e.type === "guide_think"
      && !thinkWithDelegate.has(e.event_id)
      && e.parent_event_id != null && cmdIds.has(e.parent_event_id),
  );
  const last = replies[replies.length - 1];
  return last ? { event_id: last.event_id, text: String(last.payload.text ?? "") } : null;
}

// ── 实时动态 feed:把事件流翻译成人话(图一风格)──────────────────────
type FeedTone = "user" | "guide" | "agent" | "system" | "alert";
interface FeedLine { id: number; time: string; actor: string; text: string; tone: FeedTone; }

const TONE_COLOR: Record<FeedTone, string> = {
  user: "#2e7d32", guide: "#ef6c00", agent: "#1565c0", system: "#6a1b9a", alert: "#c62828",
};

function roleFromId(id: string): string { return id.includes("#") ? id.split("#")[0] : id; }
function actorName(id: string): string {
  if (id === "user") return "你";
  if (id === "system") return "系统";
  return roleTitle(id, roleFromId(id));
}
function fmtTime(ts?: string): string {
  if (!ts) return "";
  const d = new Date(ts);
  return isNaN(d.getTime()) ? "" : d.toLocaleTimeString("zh-CN", { hour12: false });
}
function trunc(s: string, n: number): string { return s.length > n ? s.slice(0, n) + "…" : s; }

// 事件 → 一条动态;返回 null = 内部噪声(tool_intent/guide_assign 等)不显示
function eventToFeed(e: TerraEvent): FeedLine | null {
  const p = e.payload ?? {};
  const S = (v: unknown) => String(v ?? "");
  const b = { id: e.event_id, time: fmtTime(e.ts) };
  switch (e.type) {
    case "user_command": return { ...b, actor: "你", text: `「${S(p.text)}」`, tone: "user" };
    case "guide_think": return { ...b, actor: "向导", text: trunc(S(p.text), 140), tone: "guide" };
    case "guide_delegate": {
      const tc = p.task_card as { objective?: string } | undefined;
      return { ...b, actor: "向导", text: `启动任务:${trunc(S(tc?.objective), 70)}`, tone: "guide" };
    }
    case "npc_think": return { ...b, actor: actorName(e.agent), text: trunc(S(p.text), 140), tone: "agent" };
    case "tool_done": return { ...b, actor: actorName(e.agent), text: `执行 ${S(p.tool)} ${p.status === "ok" ? "✓" : "✗"}`, tone: "agent" };
    case "review_request": return { ...b, actor: actorName(e.agent), text: "提交产物,等待审查", tone: "agent" };
    case "verify_run": return { ...b, actor: "验证", text: `运行 ${trunc(S(p.command), 50)} → ${p.passed ? "通过 ✓" : "未通过 ✗"}`, tone: "system" };
    case "review_verdict": return { ...b, actor: actorName(S(p.reviewer)), text: `审查${p.verdict === "pass" ? "通过 ✓" : "打回 ✗"}${p.notes ? ":" + trunc(S(p.notes), 70) : ""}`, tone: p.verdict === "pass" ? "agent" : "alert" };
    case "merge": return { ...b, actor: "系统", text: p.result === "success" ? `已合并 ${S(p.task_id)} ✓` : `合并冲突 ${S(p.task_id)}`, tone: "system" };
    case "hitl_request": return { ...b, actor: "🔔 需要你", text: trunc(S(p.question), 110), tone: "alert" };
    case "hitl_response": return { ...b, actor: "你", text: `回应:${p.decision === "answer" ? "整改 " + trunc(S(p.text), 50) : p.decision === "reject" ? "放弃任务" : "批准"}`, tone: "user" };
    case "error": return { ...b, actor: actorName(S(p.agent_ref) || e.agent), text: `出错:${trunc(S(p.message), 70)}`, tone: "alert" };
    default: return null;
  }
}

export function App() {
  const [base, setBase] = useState(DEFAULT_BASE);
  const [session, setSession] = useState("default");
  const [connected, setConnected] = useState(false);
  const [phase, setPhase] = useState<Phase | "idle">("idle");
  const [count, setCount] = useState(0);
  const [snap, setSnap] = useState<ViewSnapshot>(() => project([]));
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const [cmd, setCmd] = useState("");           // 指令输入框
  const [model, setModel] = useState("");       // 模型选择(全局覆盖,空=角色默认)
  const [busy, setBusy] = useState(false);      // 写请求进行中
  // 未回应的 HITL 卡口(at_glass 闪烁时弹回应框)
  const [openHitl, setOpenHitl] = useState<{ event_id: number; question: string; task_id?: string } | null>(null);
  const [hitlText, setHitlText] = useState("");
  const [guideReply, setGuideReply] = useState<{ event_id: number; text: string } | null>(null);
  const [feed, setFeed] = useState<FeedLine[]>([]);   // 实时动态流(图一风格)
  const feedRef = useRef<HTMLDivElement | null>(null);

  const subRef = useRef<{ close: () => void } | null>(null);
  const townHostRef = useRef<HTMLDivElement | null>(null);
  const gameRef = useRef<Phaser.Game | null>(null);
  const sceneRef = useRef<TownScene | null>(null);
  const eventsRef = useRef<TerraEvent[]>([]);

  useEffect(() => {
    if (townHostRef.current && !gameRef.current) {
      const { game, scene } = createTown(townHostRef.current);
      gameRef.current = game; sceneRef.current = scene;
      scene.setHoverCallback((id, pos) => {
        setHover(id && pos ? { id, x: pos.x, y: pos.y } : null);
      });
      queueMicrotask(() => sceneRef.current?.applySnapshot(project([])));
    }
    return () => {
      subRef.current?.close();
      subRef.current = null;
      gameRef.current?.destroy(true);
      gameRef.current = null;
      sceneRef.current = null;
    };
  }, []);

  useEffect(() => {  // 新动态进来自动滚到底
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [feed]);

  function pushEvent(e: TerraEvent) {
    eventsRef.current.push(e);
    setCount(eventsRef.current.length);
    const next = project(eventsRef.current);
    setSnap(next);
    sceneRef.current?.applySnapshot(next);
    setOpenHitl(computeOpenHitl(eventsRef.current));  // 刷新待回应 HITL
    setGuideReply(computeGuideReply(eventsRef.current));  // 刷新向导对话回复(ADR-023)
    const line = eventToFeed(e);                      // 追加实时动态
    if (line) setFeed((prev) => [...prev, line]);
  }

  async function sendCommand() {
    const text = cmd.trim();
    if (!text || busy) return;
    setBusy(true);
    try { await postCommand(base, session, text, model); setCmd(""); }
    catch (err) { alert("发送失败:" + err); }
    finally { setBusy(false); }
  }

  async function answerHitl(decision: "answer" | "reject") {
    if (!openHitl || busy) return;
    setBusy(true);
    try {
      await postHitl(base, session, openHitl.event_id, decision,
                     decision === "answer" ? hitlText.trim() : undefined);
      setHitlText(""); setOpenHitl(null);  // 乐观清除;WS 收到 hitl_response 后亦会同步
    } catch (err) { alert("回应失败:" + err); }
    finally { setBusy(false); }
  }

  function connect() {
    subRef.current?.close();
    eventsRef.current = [];
    setCount(0);
    setSnap(project([]));
    setOpenHitl(null);
    setGuideReply(null);
    setFeed([]);
    setPhase("catchup");
    setConnected(true);
    subRef.current = subscribe(base, session, {
      onEvent: (e, ph) => { setPhase(ph); pushEvent(e); },
      onCaughtUp: () => setPhase("live"),
      onClose: () => setConnected(false),
      onError: () => setConnected(false),
    });
  }

  const hoveredNpc: NpcSnapshot | null = hover ? snap.npcs[hover.id] ?? null : null;
  const tasks = useMemo(
    () => Object.entries(snap.task_board).sort(
      (a, b) => a[1].last_event_id - b[1].last_event_id),
    [snap.task_board],
  );

  return (
    <div style={{ padding: 16, fontFamily: "system-ui,sans-serif" }}>
      <h2 style={{ margin: "4px 0 12px" }}>
        TerraWorks · 像素小镇 <small style={{ color: "#888" }}>(M4 双向交互)</small>
      </h2>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
        <input value={base} onChange={(e) => setBase(e.target.value)}
               style={{ flex: 2, padding: 6 }} />
        <input value={session} onChange={(e) => setSession(e.target.value)}
               style={{ flex: 1, padding: 6 }} placeholder="session id" />
        <button onClick={connect} style={{ padding: "6px 16px" }}>连接</button>
        <span style={{ marginLeft: 8 }}>
          {connected ? "已连接" : "未连接"} ｜ 阶段:
          <b style={{ color: phase === "live" ? "#2a7" : phase === "catchup" ? "#a72" : "#888" }}>
            {" "}{phase}
          </b>
          {" "}｜ 事件:<b>{count}</b>
        </span>
      </div>

      {/* HITL 回应面板:有未回应的卡口时高亮(对应小镇里 at_glass 闪烁) */}
      {openHitl && (
        <div style={{
          marginBottom: 10, padding: 12, border: "2px solid #e74c3c", borderRadius: 6,
          background: "#fff5f5",
        }}>
          <div style={{ marginBottom: 6 }}>
            🔔 <b>需要你回应</b>
            {openHitl.task_id && <code style={{ marginLeft: 6 }}>{openHitl.task_id}</code>}
            <div style={{ color: "#666", marginTop: 2 }}>{openHitl.question}</div>
          </div>
          <textarea value={hitlText} onChange={(e) => setHitlText(e.target.value)}
                    placeholder="给整改指引(选 answer 时填)…"
                    style={{ width: "100%", minHeight: 48, padding: 6, boxSizing: "border-box" }} />
          <div style={{ marginTop: 6, display: "flex", gap: 8 }}>
            <button disabled={busy || !hitlText.trim()} onClick={() => answerHitl("answer")}
                    style={{ padding: "6px 16px" }}>提交整改(重做)</button>
            <button disabled={busy} onClick={() => answerHitl("reject")}
                    style={{ padding: "6px 16px", color: "#c0392b" }}>放弃该任务</button>
          </div>
        </div>
      )}

      {/* 指令栏:跟向导下新任务 / 追加指令 → POST /command */}
      <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        <input value={cmd} onChange={(e) => setCmd(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") sendCommand(); }}
               placeholder="跟向导下个任务…(回车发送)"
               style={{ flex: 1, padding: 8 }} disabled={busy} />
        <select value={model} onChange={(e) => setModel(e.target.value)} disabled={busy}
                title="选择模型(全局覆盖所有 NPC)"
                style={{ padding: 8, border: "1px solid #ccc", borderRadius: 4 }}>
          {MODELS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
        <button onClick={sendCommand} disabled={busy || !cmd.trim()}
                style={{ padding: "8px 20px" }}>{busy ? "…" : "下指令"}</button>
      </div>

      {/* 向导对话回复(ADR-023):非任务输入(问候/闲聊)时向导直接回你一句,不建任务卡 */}
      {guideReply && (
        <div style={{
          marginBottom: 10, padding: "8px 12px", background: "#fff8e1",
          border: "1px solid #ffe082", borderRadius: 6, fontSize: 13, color: "#5a4f3a",
        }}>
          <b>向导</b>:{guideReply.text}
        </div>
      )}

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <div style={{ position: "relative" }}>
          <div ref={townHostRef} style={{ width: 880, height: 560, border: "1px solid #ddd" }} />
          {hoveredNpc && hover && (
            <ThinkTooltip
              x={hover.x} y={hover.y} id={hover.id} npc={hoveredNpc}
            />
          )}
        </div>

        {/* 右侧:实时动态(图一风格)+ 紧凑任务板 */}
        <aside style={{ width: 320, fontSize: 13, display: "flex", flexDirection: "column", height: 560 }}>
          <h3 style={{ margin: "0 0 6px", display: "flex", alignItems: "center", gap: 6 }}>
            实时动态 <span style={{ fontSize: 11, color: "#4caf50" }}>● 直播中</span>
          </h3>
          <div ref={feedRef} style={{
            flex: 1, overflowY: "auto", border: "1px solid #eee", borderRadius: 6,
            padding: 8, background: "#fafafa", display: "flex", flexDirection: "column", gap: 6,
          }}>
            {feed.length === 0 && <p style={{ color: "#aaa", margin: 0 }}>暂无动态——跟向导下个任务试试</p>}
            {feed.map((l) => (
              <div key={l.id} style={{ display: "flex", gap: 6, lineHeight: 1.4 }}>
                <span style={{ color: "#bbb", fontSize: 11, flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>{l.time}</span>
                <span>
                  <b style={{ color: TONE_COLOR[l.tone] }}>{l.actor}</b>
                  <span style={{ color: "#444" }}> {l.text}</span>
                </span>
              </div>
            ))}
          </div>

          {/* 紧凑任务板 */}
          <h4 style={{ margin: "10px 0 6px" }}>任务板 ({tasks.length})</h4>
          {tasks.length === 0
            ? <p style={{ color: "#aaa", margin: 0 }}>暂无任务</p>
            : (
              <ul style={{ listStyle: "none", padding: 0, margin: 0, maxHeight: 130, overflowY: "auto" }}>
                {tasks.map(([tid, slot]) => (
                  <li key={tid} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                    <code style={{ fontSize: 12 }}>{tid}</code>
                    <span style={{
                      background: STATUS_COLOR[slot.status], color: "white",
                      padding: "1px 6px", borderRadius: 3, fontSize: 11,
                    }}>{slot.status}</span>
                  </li>
                ))}
              </ul>
            )}
        </aside>
      </div>

      <p style={{ fontSize: 12, color: "#888", marginTop: 8 }}>
        💡 悬停任意小人,看它的<b>职位</b>和此刻的<b>思考</b>。脚边的小圆点是它的状态——
        <span style={{ color: "#4a8c4a" }}>●空闲</span>{" "}
        <span style={{ color: "#2e86c1" }}>●干活中</span>{" "}
        <span style={{ color: "#9b59b6" }}>●验证</span>{" "}
        <span style={{ color: "#16a085" }}>●审查</span>{" "}
        <span style={{ color: "#e74c3c" }}>●需要你</span>。
      </p>
    </div>
  );
}

function ThinkTooltip({ x, y, id, npc }: { x: number; y: number; id: string; npc: NpcSnapshot }) {
  // Phaser 画布相对坐标 → 浮窗定位(画布偏移由父容器 position:relative 处理)
  return (
    <div
      style={{
        position: "absolute", left: x + 30, top: y - 10, maxWidth: 280, pointerEvents: "none",
        background: "rgba(30,30,40,0.92)", color: "#eee", padding: "8px 10px",
        borderRadius: 6, fontSize: 12, lineHeight: 1.5, zIndex: 100,
        boxShadow: "0 4px 12px rgba(0,0,0,0.2)",
      }}
    >
      <div style={{ marginBottom: 4 }}>
        <b>{roleTitle(id, npc.sprite_key)}</b>{" "}
        <small style={{ color: "#aaa" }}>
          {npc.state}{npc.task_id && <> · <code>{npc.task_id}</code></>}
        </small>
      </div>
      {npc.think ? (
        <div>
          <small style={{ color: "#aaa" }}>think #{npc.think.event_id}</small>
          <div style={{ marginTop: 2, whiteSpace: "pre-wrap" }}>{npc.think.text}</div>
        </div>
      ) : (
        <small style={{ color: "#888" }}>(暂无 think)</small>
      )}
    </div>
  );
}
