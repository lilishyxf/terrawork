// M3-5 完整 View:订阅事件 → 投影 → Phaser 小镇 + 悬停看 think + 任务板侧栏。
import { useEffect, useMemo, useRef, useState } from "react";
import { subscribe, postCommand, postHitl, type TerraEvent, type Phase } from "./ipc/subscribe";
import { project, type ViewSnapshot, type NpcSnapshot, type TaskStatus } from "./game/protocol/projection";
import { createTown, type TownScene } from "./game/town";
import type Phaser from "phaser";

const DEFAULT_BASE = "http://127.0.0.1:8000";

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

export function App() {
  const [base, setBase] = useState(DEFAULT_BASE);
  const [session, setSession] = useState("default");
  const [connected, setConnected] = useState(false);
  const [phase, setPhase] = useState<Phase | "idle">("idle");
  const [count, setCount] = useState(0);
  const [snap, setSnap] = useState<ViewSnapshot>(() => project([]));
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const [cmd, setCmd] = useState("");           // 指令输入框
  const [busy, setBusy] = useState(false);      // 写请求进行中
  // 未回应的 HITL 卡口(at_glass 闪烁时弹回应框)
  const [openHitl, setOpenHitl] = useState<{ event_id: number; question: string; task_id?: string } | null>(null);
  const [hitlText, setHitlText] = useState("");

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

  function pushEvent(e: TerraEvent) {
    eventsRef.current.push(e);
    setCount(eventsRef.current.length);
    const next = project(eventsRef.current);
    setSnap(next);
    sceneRef.current?.applySnapshot(next);
    setOpenHitl(computeOpenHitl(eventsRef.current));  // 刷新待回应 HITL
  }

  async function sendCommand() {
    const text = cmd.trim();
    if (!text || busy) return;
    setBusy(true);
    try { await postCommand(base, session, text); setCmd(""); }
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
        <button onClick={sendCommand} disabled={busy || !cmd.trim()}
                style={{ padding: "8px 20px" }}>{busy ? "…" : "下指令"}</button>
      </div>

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <div style={{ position: "relative" }}>
          <div ref={townHostRef} style={{ width: 880, height: 560, border: "1px solid #ddd" }} />
          {hoveredNpc && hover && (
            <ThinkTooltip
              x={hover.x} y={hover.y} id={hover.id} npc={hoveredNpc}
            />
          )}
        </div>

        {/* 任务板侧栏 */}
        <aside style={{ width: 240, fontSize: 13 }}>
          <h3 style={{ margin: "0 0 8px" }}>任务板 ({tasks.length})</h3>
          {tasks.length === 0 && <p style={{ color: "#aaa" }}>暂无任务</p>}
          <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
            {tasks.map(([tid, slot]) => (
              <li key={tid} style={{ marginBottom: 6, padding: 6, border: "1px solid #eee", borderRadius: 4 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <code style={{ fontSize: 12 }}>{tid}</code>
                  <span style={{
                    background: STATUS_COLOR[slot.status], color: "white",
                    padding: "2px 6px", borderRadius: 3, fontSize: 11,
                  }}>{slot.status}</span>
                </div>
                {slot.builder && <small style={{ color: "#888" }}>→ {slot.builder}</small>}
              </li>
            ))}
          </ul>
        </aside>
      </div>

      <p style={{ fontSize: 11, color: "#999", marginTop: 8 }}>
        精灵 = 职业（缺图回退色块，PNG 放 public/sprites/&lt;key&gt;.png）｜
        角上<b>状态色点</b>:idle🟢 decomposing🟡 thinking🟣 working🔵 rework🔴
        awaiting_review🟠 verifying🟪 reviewing🟩 hitl🔥 error⚠️ ｜
        悬停看 think（ADR-002）｜ HITL 屏幕前闪烁 ｜ merge 钟楼敲钟。
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
        <b>{id}</b>{" "}
        <small style={{ color: "#aaa" }}>
          {npc.kind} · {npc.state} · {npc.zone}
          {npc.task_id && <> · <code>{npc.task_id}</code></>}
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
