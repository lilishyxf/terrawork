// M3-5 完整 View:订阅事件 → 投影 → Phaser 小镇 + 悬停看 think + 任务板侧栏。
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { subscribe, postCommand, postHitl, fetchWorkspaceTree, fetchWorkspaceFile, getWorkspace, pickWorkspace,
  getProviders, upsertProvider, deleteProvider, setActiveProvider, type ProvidersState,
  type TerraEvent, type Phase } from "./ipc/subscribe";
import { project, agentName, type ViewSnapshot, type NpcSnapshot, type TaskStatus } from "./game/protocol/projection";
import { createTown, type TownScene } from "./game/town";
import type Phaser from "phaser";

const DEFAULT_BASE = "http://127.0.0.1:8000";


// 深色主题调色板(图二风格)
const T = {
  bg: "#0f1115", panel: "#171a21", panel2: "#1e222b", border: "#2b313c",
  text: "#e6e8ec", dim: "#9aa1ad", faint: "#6b7280", accent: "#5b8cff",
  inputBg: "#11141b",
};
const inputCss: CSSProperties = {
  background: T.inputBg, color: T.text, border: `1px solid ${T.border}`,
  borderRadius: 10, padding: "9px 12px", outline: "none", fontSize: 14,
};
const btnCss: CSSProperties = {
  background: T.accent, color: "#fff", border: "none", borderRadius: 10,
  padding: "9px 18px", cursor: "pointer", fontWeight: 600,
};
const cardCss: CSSProperties = {
  background: T.panel, border: `1px solid ${T.border}`, borderRadius: 12,
};

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

function actorName(id: string): string {
  if (id === "user") return "你";
  if (id === "system") return "系统";
  return agentName(id);   // 人名(如"苏晴"),取不到则原样
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
    case "verify_run": return { ...b, actor: actorName(e.agent), text: `验证 ${trunc(S(p.command), 46)} → ${p.passed ? "通过 ✓" : "未通过 ✗"}`, tone: "system" };
    case "review_verdict": return { ...b, actor: actorName(S(p.reviewer)), text: `审查${p.verdict === "pass" ? "通过 ✓" : "打回 ✗"}${p.notes ? ":" + trunc(S(p.notes), 70) : ""}`, tone: p.verdict === "pass" ? "agent" : "alert" };
    case "merge": return { ...b, actor: "系统", text: p.result === "success" ? `已合并 ${S(p.task_id)} ✓` : `合并冲突 ${S(p.task_id)}`, tone: "system" };
    case "hitl_request": return { ...b, actor: "🔔 需要你", text: trunc(S(p.question), 110), tone: "alert" };
    case "hitl_response": return { ...b, actor: "你", text: `回应:${p.decision === "answer" ? "整改 " + trunc(S(p.text), 50) : p.decision === "reject" ? "放弃任务" : "批准"}`, tone: "user" };
    case "error": return { ...b, actor: actorName(S(p.agent_ref) || e.agent), text: `出错:${trunc(S(p.message), 70)}`, tone: "alert" };
    default: return null;
  }
}

export function App() {
  const base = DEFAULT_BASE;        // 后端地址固定(单机壳);不再让用户手填
  const session = "default";
  const [connected, setConnected] = useState(false);
  const [phase, setPhase] = useState<Phase | "idle">("idle");
  const [count, setCount] = useState(0);
  const [snap, setSnap] = useState<ViewSnapshot>(() => project([]));
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const [cmd, setCmd] = useState("");           // 指令输入框
  const [showSettings, setShowSettings] = useState(false);                 // 供应商设置面板
  const [providers, setProviders] = useState<ProvidersState>({ active: null, providers: [] });
  const [busy, setBusy] = useState(false);      // 写请求进行中
  // 未回应的 HITL 卡口(at_glass 闪烁时弹回应框)
  const [openHitl, setOpenHitl] = useState<{ event_id: number; question: string; task_id?: string } | null>(null);
  const [hitlText, setHitlText] = useState("");
  const [guideReply, setGuideReply] = useState<{ event_id: number; text: string } | null>(null);
  const [feed, setFeed] = useState<FeedLine[]>([]);   // 实时动态流(图一风格)
  const feedRef = useRef<HTMLDivElement | null>(null);
  // 成果可视化:小镇 / 文件 / 预览
  const [view, setView] = useState<"town" | "files" | "preview">("town");
  const [files, setFiles] = useState<string[]>([]);
  const [curFile, setCurFile] = useState<string | null>(null);
  const [fileText, setFileText] = useState("");
  const [previewNonce, setPreviewNonce] = useState(0);   // 刷新 iframe 用
  const [workspace, setWorkspaceState] = useState("");   // 当前目标仓库路径
  const [attachments, setAttachments] = useState<{ name: string; content: string }[]>([]);  // 附件(文本/代码文件)
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const reconnectRef = useRef<number | null>(null);
  const cursorRef = useRef(0);        // 已收到的最大 event_id(重连续传用)
  const genRef = useRef(0);           // 连接代次:只认最新连接的断开

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
    connect();   // 打开即自动连接(无需手动点"连接")
    loadProviders();
    return () => {
      genRef.current++;   // 让当前连接的 onClose 失效,卸载/重挂不触发重连
      if (reconnectRef.current) { clearTimeout(reconnectRef.current); reconnectRef.current = null; }
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
    try { await postCommand(base, session, text, undefined, attachments); setCmd(""); setAttachments([]); }
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

  // 打开连接。resume=true:从游标续传(只补新事件,不清空、不重放)——断线自愈时用;
  // resume=false:全量(初次/手动重连)。代次(gen)守卫:只有"最新连接"的断开才触发重连,
  // 主动关闭(重连/卸载/StrictMode 双挂载)的旧连接断开一律忽略,杜绝重连抖动与整页重刷。
  function _open(resume: boolean) {
    const myGen = ++genRef.current;
    subRef.current?.close();           // 旧连接 gen 已过期,其 onClose 会被忽略
    if (!resume) {
      eventsRef.current = [];
      cursorRef.current = 0;
      setCount(0); setSnap(project([])); setOpenHitl(null); setGuideReply(null); setFeed([]);
    }
    setPhase(resume ? "live" : "catchup");
    setConnected(true);
    subRef.current = subscribe(base, session, {
      onEvent: (e, ph) => { cursorRef.current = e.event_id ?? cursorRef.current; setPhase(ph); pushEvent(e); },
      onCaughtUp: () => setPhase("live"),
      onClose: () => { if (myGen === genRef.current) { setConnected(false); _scheduleReconnect(); } },
      onError: () => { if (myGen === genRef.current) { setConnected(false); _scheduleReconnect(); } },
    }, { since: resume ? cursorRef.current : 0 });
  }

  function connect() { _open(false); }   // 手动/初次:全量

  // 断线自愈:2 秒后从游标续传(只补新事件,界面不闪、不重放历史)
  function _scheduleReconnect() {
    if (reconnectRef.current) return;
    reconnectRef.current = window.setTimeout(() => {
      reconnectRef.current = null;
      _open(true);
    }, 2000);
  }

  async function loadTree() {
    try { setFiles(await fetchWorkspaceTree(base)); } catch { setFiles([]); }
    try { setWorkspaceState((await getWorkspace(base)).path); } catch { /* 忽略 */ }
  }
  async function openFile(path: string) {
    setCurFile(path);
    try { setFileText(await fetchWorkspaceFile(base, path)); }
    catch (e) { setFileText("(读取失败:" + e + ")"); }
  }
  function switchView(v: "town" | "files" | "preview") {
    setView(v);
    if (v === "files") loadTree();
    if (v === "preview") setPreviewNonce((n) => n + 1);
  }
  async function changeWorkspace() {
    try {
      const r = await pickWorkspace(base);   // 后端弹系统原生文件夹框
      if (r.cancelled) return;
      setWorkspaceState(r.path);
      setCurFile(null); setFileText(""); loadTree(); setPreviewNonce((n) => n + 1);
    } catch (e) { alert("切换工作区失败:" + e); }
  }
  async function loadProviders() {
    try { setProviders(await getProviders(base)); } catch { /* 后端未起时忽略 */ }
  }
  async function addFiles(list: FileList | null) {
    if (!list) return;
    const added: { name: string; content: string }[] = [];
    for (const f of Array.from(list)) {
      if (f.size > 5_000_000) { alert(`${f.name} 太大(>5MB),跳过`); continue; }
      added.push({ name: f.name, content: await f.text() });
    }
    setAttachments((prev) => [...prev, ...added]);
    if (fileInputRef.current) fileInputRef.current.value = "";  // 允许重复选同一文件
  }

  const hoveredNpc: NpcSnapshot | null = hover ? snap.npcs[hover.id] ?? null : null;
  const tasks = useMemo(
    () => Object.entries(snap.task_board).sort(
      (a, b) => a[1].last_event_id - b[1].last_event_id),
    [snap.task_board],
  );

  return (
    <div style={{ padding: 16, fontFamily: "system-ui,sans-serif", background: T.bg, color: T.text, minHeight: "100vh" }}>
      <h2 style={{ margin: "4px 0 12px", fontWeight: 700, display: "flex", alignItems: "center", gap: 12 }}>
        <img src="/logo.png" alt="TerraWorks" style={{ height: 46, borderRadius: 8, display: "block" }} />
        <small style={{ color: T.faint, fontWeight: 400 }}>像素小镇 · 双向编排工作台</small>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 13, color: T.dim, fontWeight: 400 }}>
          <span style={{ color: connected ? "#3ddc84" : "#e0524a" }}>●</span>{" "}
          {connected ? (phase === "live" ? "直播中" : "同步中") : "连接中…"}
          {" "}· 事件 <b style={{ color: T.text }}>{count}</b>
          {!connected && (
            <button onClick={connect} style={{
              marginLeft: 8, background: "transparent", color: T.accent,
              border: `1px solid ${T.border}`, borderRadius: 8, padding: "2px 10px", cursor: "pointer",
            }}>重连</button>
          )}
        </span>
      </h2>

      {/* HITL 回应面板:有未回应的卡口时高亮(对应小镇里 at_glass 闪烁) */}
      {openHitl && (
        <div style={{
          marginBottom: 10, padding: 12, border: "1px solid #e0524a", borderRadius: 12,
          background: "#241518",
        }}>
          <div style={{ marginBottom: 6 }}>
            🔔 <b style={{ color: "#ff8a80" }}>需要你回应</b>
            {openHitl.task_id && <code style={{ marginLeft: 6, color: T.dim }}>{openHitl.task_id}</code>}
            <div style={{ color: T.dim, marginTop: 2 }}>{openHitl.question}</div>
          </div>
          <textarea value={hitlText} onChange={(e) => setHitlText(e.target.value)}
                    placeholder="给整改指引(选 answer 时填)…"
                    style={{ ...inputCss, width: "100%", minHeight: 48, boxSizing: "border-box" }} />
          <div style={{ marginTop: 6, display: "flex", gap: 8 }}>
            <button disabled={busy || !hitlText.trim()} onClick={() => answerHitl("answer")}
                    style={btnCss}>提交整改(重做)</button>
            <button disabled={busy} onClick={() => answerHitl("reject")}
                    style={{ ...btnCss, background: "transparent", color: "#ff8a80", border: "1px solid #e0524a" }}>放弃该任务</button>
          </div>
        </div>
      )}

      {/* 附件 chips(随指令当上下文) */}
      {attachments.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 6 }}>
          {attachments.map((a, i) => (
            <span key={i} style={{ ...cardCss, padding: "3px 8px", fontSize: 12, color: T.dim, display: "inline-flex", gap: 6, alignItems: "center" }}>
              📄 {a.name}
              <span onClick={() => setAttachments((p) => p.filter((_, j) => j !== i))}
                style={{ cursor: "pointer", color: T.faint }}>✕</span>
            </span>
          ))}
        </div>
      )}

      {/* 指令栏:跟向导下新任务 / 追加指令 → POST /command */}
      <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        <input ref={fileInputRef} type="file" multiple style={{ display: "none" }}
               onChange={(e) => addFiles(e.target.files)} />
        <button onClick={() => fileInputRef.current?.click()} disabled={busy}
                title="附文本/代码文件当任务上下文"
                style={{ ...inputCss, borderRadius: 999, cursor: "pointer", padding: "10px 14px" }}>📎</button>
        <input value={cmd} onChange={(e) => setCmd(e.target.value)}
               onKeyDown={(e) => { if (e.key === "Enter") sendCommand(); }}
               placeholder="跟向导下个任务…(回车发送)"
               style={{ ...inputCss, flex: 1, borderRadius: 999, padding: "10px 18px" }} disabled={busy} />
        <button onClick={() => { setShowSettings(true); loadProviders(); }} disabled={busy}
                title="模型/供应商设置(API key)"
                style={{ ...inputCss, borderRadius: 999, cursor: "pointer", maxWidth: 220, overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" }}>
          ⚙️ {(() => { const a = providers.providers.find((p) => p.id === providers.active); return a ? a.name : "设置模型"; })()}
        </button>
        <button onClick={sendCommand} disabled={busy || !cmd.trim()}
                style={{ ...btnCss, borderRadius: 999, opacity: busy || !cmd.trim() ? 0.5 : 1 }}>{busy ? "…" : "下指令"}</button>
      </div>

      {/* 向导对话回复(ADR-023):非任务输入(问候/闲聊)时向导直接回你一句,不建任务卡 */}
      {guideReply && (
        <div style={{
          marginBottom: 10, padding: "9px 14px", background: "#1d2330",
          border: `1px solid ${T.border}`, borderRadius: 10, fontSize: 13, color: T.text,
        }}>
          <b style={{ color: "#ffb74d" }}>向导</b>:{guideReply.text}
        </div>
      )}

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        <div>
          {/* 标签页:小镇 / 文件 / 预览 */}
          <div style={{ display: "flex", gap: 6, marginBottom: 8, alignItems: "center" }}>
            {([["town", "🏘 小镇"], ["files", "📁 文件"], ["preview", "▶ 预览"]] as const).map(([v, label]) => (
              <button key={v} onClick={() => switchView(v)} style={{
                padding: "6px 14px", borderRadius: 8, cursor: "pointer", fontWeight: 600,
                background: view === v ? T.accent : "transparent",
                color: view === v ? "#fff" : T.dim,
                border: `1px solid ${view === v ? T.accent : T.border}`,
              }}>{label}</button>
            ))}
            {view !== "town" && (
              <button onClick={() => (view === "files" ? loadTree() : setPreviewNonce((n) => n + 1))}
                style={{ padding: "6px 12px", borderRadius: 8, cursor: "pointer", background: "transparent", color: T.dim, border: `1px solid ${T.border}` }}>
                刷新
              </button>
            )}
            <span style={{ flex: 1 }} />
            <button onClick={changeWorkspace} title="选择 NPC 干活的项目仓库"
              style={{ padding: "6px 12px", borderRadius: 8, cursor: "pointer", background: "transparent", color: T.dim, border: `1px solid ${T.border}` }}>
              📂 项目{workspace ? ":" + workspace.split(/[\\/]/).pop() : ""}
            </button>
          </div>

          <div style={{ position: "relative", width: 880, height: 560 }}>
            {/* 小镇:常驻不卸载(保留 Phaser),切走时隐藏 */}
            <div ref={townHostRef} style={{
              width: 880, height: 560, border: `1px solid ${T.border}`, borderRadius: 12,
              overflow: "hidden", display: view === "town" ? "block" : "none",
            }} />
            {view === "town" && hoveredNpc && hover && (
              <ThinkTooltip x={hover.x} y={hover.y} id={hover.id} npc={hoveredNpc} />
            )}

            {/* 文件:树 + 代码 */}
            {view === "files" && (
              <div style={{ ...cardCss, position: "absolute", inset: 0, display: "flex", overflow: "hidden" }}>
                <div style={{ width: 240, borderRight: `1px solid ${T.border}`, overflowY: "auto", padding: 8, flexShrink: 0 }}>
                  {files.length === 0 && <p style={{ color: T.faint, margin: 4, fontSize: 12 }}>暂无文件(任务合并后出现)</p>}
                  {files.map((f) => (
                    <div key={f} onClick={() => openFile(f)} style={{
                      padding: "4px 6px", borderRadius: 6, cursor: "pointer", fontSize: 12, marginBottom: 2,
                      color: curFile === f ? "#fff" : T.dim,
                      background: curFile === f ? T.accent : "transparent",
                    }}>{f}</div>
                  ))}
                </div>
                <pre style={{ flex: 1, margin: 0, overflow: "auto", padding: 12, fontSize: 12, color: T.text, whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
                  {curFile ? fileText : "← 左侧选个文件看代码"}
                </pre>
              </div>
            )}

            {/* 预览:内嵌运行沙箱里的 index.html */}
            {view === "preview" && (
              <iframe key={previewNonce} title="preview"
                src={`${base}/workspace/raw/index.html?t=${previewNonce}`}
                style={{ width: 880, height: 560, border: `1px solid ${T.border}`, borderRadius: 12, background: "#fff" }} />
            )}
          </div>
        </div>

        {/* 右侧:实时动态(图一风格)+ 紧凑任务板 */}
        <aside style={{ width: 320, fontSize: 13, display: "flex", flexDirection: "column", height: 560 }}>
          <h3 style={{ margin: "0 0 6px", display: "flex", alignItems: "center", gap: 6, color: T.text }}>
            实时动态 <span style={{ fontSize: 11, color: "#3ddc84" }}>● 直播中</span>
          </h3>
          <div ref={feedRef} style={{
            ...cardCss, flex: 1, overflowY: "auto",
            padding: 10, display: "flex", flexDirection: "column", gap: 7,
          }}>
            {feed.length === 0 && <p style={{ color: T.faint, margin: 0 }}>暂无动态——跟向导下个任务试试</p>}
            {feed.map((l) => (
              <div key={l.id} style={{ display: "flex", gap: 6, lineHeight: 1.45 }}>
                <span style={{ color: T.faint, fontSize: 11, flexShrink: 0, fontVariantNumeric: "tabular-nums" }}>{l.time}</span>
                <span>
                  <b style={{ color: TONE_COLOR[l.tone] }}>{l.actor}</b>
                  <span style={{ color: T.dim }}> {l.text}</span>
                </span>
              </div>
            ))}
          </div>

          {/* 紧凑任务板 */}
          <h4 style={{ margin: "12px 0 6px", color: T.text }}>任务板 ({tasks.length})</h4>
          {tasks.length === 0
            ? <p style={{ color: T.faint, margin: 0 }}>暂无任务</p>
            : (
              <ul style={{ ...cardCss, listStyle: "none", padding: 8, margin: 0, maxHeight: 130, overflowY: "auto" }}>
                {tasks.map(([tid, slot]) => (
                  <li key={tid} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                    <code style={{ fontSize: 12, color: T.dim }}>{tid}</code>
                    <span style={{
                      background: STATUS_COLOR[slot.status], color: "white",
                      padding: "1px 7px", borderRadius: 999, fontSize: 11,
                    }}>{slot.status}</span>
                  </li>
                ))}
              </ul>
            )}
        </aside>
      </div>

      <p style={{ fontSize: 12, color: T.faint, marginTop: 10 }}>
        💡 悬停任意小人,看它的<b style={{ color: T.dim }}>职能</b>和此刻的<b style={{ color: T.dim }}>思考</b>。脚边小圆点是状态——
        <span style={{ color: "#4a8c4a" }}>●空闲</span>{" "}
        <span style={{ color: "#3a9bdc" }}>●干活中</span>{" "}
        <span style={{ color: "#9b59b6" }}>●验证</span>{" "}
        <span style={{ color: "#16a085" }}>●审查</span>{" "}
        <span style={{ color: "#e74c3c" }}>●需要你</span>。
      </p>

      {showSettings && (
        <SettingsModal base={base} providers={providers} setProviders={setProviders}
                       onClose={() => setShowSettings(false)} />
      )}
    </div>
  );
}

// 供应商配置面板(cc-switch 式):多配置 + 一键切换 + 增删改
function SettingsModal({ base, providers, setProviders, onClose }: {
  base: string; providers: ProvidersState; setProviders: (s: ProvidersState) => void; onClose: () => void;
}) {
  const blank = { id: "", name: "", base_url: "", model: "", api_key: "" };
  const [form, setForm] = useState(blank);
  const [busy, setBusy] = useState(false);
  const editing = !!form.id;

  async function save() {
    if (!form.name.trim() || !form.base_url.trim() || !form.model.trim()) { alert("名称/地址/模型必填"); return; }
    setBusy(true);
    try {
      const next = await upsertProvider(base, {
        id: form.id || undefined, name: form.name, base_url: form.base_url,
        model: form.model, api_key: form.api_key || undefined,
      });
      setProviders(next); setForm(blank);
    } catch (e) { alert("保存失败:" + e); } finally { setBusy(false); }
  }
  async function activate(id: string) { try { setProviders(await setActiveProvider(base, id)); } catch (e) { alert(e); } }
  async function remove(id: string) {
    if (!confirm("删除这个供应商配置?")) return;
    try { setProviders(await deleteProvider(base, id)); } catch (e) { alert(e); }
  }

  const fieldCss: CSSProperties = { ...inputCss, width: "100%", marginBottom: 8, boxSizing: "border-box" };
  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 200,
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        ...cardCss, width: 560, maxHeight: "86vh", overflow: "auto", padding: 18, color: T.text,
      }}>
        <div style={{ display: "flex", alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>模型 / 供应商</h3>
          <span style={{ flex: 1 }} />
          <button onClick={onClose} style={{ background: "transparent", color: T.dim, border: "none", fontSize: 18, cursor: "pointer" }}>✕</button>
        </div>

        {/* 已有配置列表 */}
        {providers.providers.length === 0 && <p style={{ color: T.faint }}>还没有配置,在下面新增一个。</p>}
        {providers.providers.map((p) => (
          <div key={p.id} style={{
            ...cardCss, padding: 10, marginBottom: 8, display: "flex", alignItems: "center", gap: 10,
            borderColor: p.id === providers.active ? T.accent : T.border,
          }}>
            <input type="radio" checked={p.id === providers.active} onChange={() => activate(p.id)} title="设为当前" />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 600 }}>{p.name} {p.id === providers.active && <span style={{ color: "#3ddc84", fontSize: 12 }}>● 当前</span>}</div>
              <div style={{ fontSize: 12, color: T.dim, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {p.model} · {p.base_url} · {p.key_masked}
              </div>
            </div>
            <button onClick={() => setForm({ id: p.id, name: p.name, base_url: p.base_url, model: p.model, api_key: "" })}
              style={{ background: "transparent", color: T.dim, border: `1px solid ${T.border}`, borderRadius: 6, padding: "3px 8px", cursor: "pointer" }}>编辑</button>
            <button onClick={() => remove(p.id)}
              style={{ background: "transparent", color: "#ff8a80", border: "1px solid #e0524a", borderRadius: 6, padding: "3px 8px", cursor: "pointer" }}>删</button>
          </div>
        ))}

        {/* 新增 / 编辑表单 */}
        <div style={{ ...cardCss, padding: 12, marginTop: 12 }}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>{editing ? "编辑配置" : "新增配置"}</div>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="名称(如 DeepSeek 官方)" style={fieldCss} />
          <input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder="API 地址 base_url(如 https://api.deepseek.com/v1)" style={fieldCss} />
          <input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder="模型 id(如 deepseek-v4-pro)" style={fieldCss} />
          <input value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} type="password"
            placeholder={editing ? "API key(留空=不改)" : "API key"} style={fieldCss} />
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={save} disabled={busy} style={btnCss}>{busy ? "…" : editing ? "保存修改" : "添加"}</button>
            {editing && <button onClick={() => setForm(blank)} style={{ ...btnCss, background: "transparent", color: T.dim, border: `1px solid ${T.border}` }}>取消编辑</button>}
          </div>
        </div>
        <p style={{ fontSize: 12, color: T.faint, marginTop: 10 }}>
          选中即全局生效(向导+所有 NPC 用它)。任意 OpenAI 兼容端点(DeepSeek / 各类网关 / OpenAI)。key 本地存,不外传。
        </p>
      </div>
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
        <b>{agentName(id)}</b>{" "}
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
