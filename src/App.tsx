// M3-3 连通性骨架:订阅 Session 事件流(catch-up→live),把事件流打到屏幕。
// 证明前后端打通;像素渲染(Phaser)在 M3-4。
import { useEffect, useRef, useState } from "react";
import { subscribe, type TerraEvent, type Phase } from "./ipc/subscribe";

const DEFAULT_BASE = "http://127.0.0.1:8000";

export function App() {
  const [base, setBase] = useState(DEFAULT_BASE);
  const [session, setSession] = useState("default");
  const [connected, setConnected] = useState(false);
  const [phase, setPhase] = useState<Phase | "idle">("idle");
  const [events, setEvents] = useState<TerraEvent[]>([]);
  const subRef = useRef<{ close: () => void } | null>(null);

  useEffect(() => () => subRef.current?.close(), []);

  function connect() {
    subRef.current?.close();
    setEvents([]);
    setPhase("catchup");
    setConnected(true);
    subRef.current = subscribe(base, session, {
      onEvent: (e, ph) => {
        setPhase(ph);
        setEvents((prev) => [...prev, e]);
      },
      onCaughtUp: () => setPhase("live"),
      onClose: () => setConnected(false),
      onError: () => setConnected(false),
    });
  }

  const counts = events.reduce<Record<string, number>>((acc, e) => {
    acc[e.type] = (acc[e.type] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div style={{ padding: 24, maxWidth: 820, margin: "0 auto" }}>
      <h1>TerraWorks · 像素小镇 View <small style={{ color: "#888" }}>(M3-3 连通性骨架)</small></h1>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
        <input value={base} onChange={(e) => setBase(e.target.value)} style={{ flex: 2, padding: 6 }} />
        <input value={session} onChange={(e) => setSession(e.target.value)} style={{ flex: 1, padding: 6 }} placeholder="session id" />
        <button onClick={connect} style={{ padding: "6px 16px" }}>连接</button>
      </div>
      <p>
        状态:{connected ? "已连接" : "未连接"} ｜ 阶段:
        <b style={{ color: phase === "live" ? "#2a7" : phase === "catchup" ? "#a72" : "#888" }}> {phase}</b>
        {" "}｜ 事件数:<b>{events.length}</b>
      </p>
      <div style={{ display: "flex", gap: 24 }}>
        <section style={{ flex: 1 }}>
          <h3>事件类型计数</h3>
          <ul>{Object.entries(counts).map(([t, n]) => <li key={t}>{t}: {n}</li>)}</ul>
        </section>
        <section style={{ flex: 1 }}>
          <h3>最近事件</h3>
          <ul style={{ fontSize: 13 }}>
            {events.slice(-12).reverse().map((e) => (
              <li key={e.event_id}>#{e.event_id} <b>{e.type}</b> [{e.agent}]</li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
