// M3-4 把订阅事件投影成 ViewSnapshot 并喂给 Phaser 小镇(色块版)。
// 一眼读全状态(§12 验收):看哪些区有色块、什么颜色、走到了哪。
import { useEffect, useRef, useState } from "react";
import { subscribe, type TerraEvent, type Phase } from "./ipc/subscribe";
import { project } from "./game/protocol/projection";
import { createTown, type TownScene } from "./game/town";
import type Phaser from "phaser";

const DEFAULT_BASE = "http://127.0.0.1:8000";

export function App() {
  const [base, setBase] = useState(DEFAULT_BASE);
  const [session, setSession] = useState("default");
  const [connected, setConnected] = useState(false);
  const [phase, setPhase] = useState<Phase | "idle">("idle");
  const [count, setCount] = useState(0);

  const subRef = useRef<{ close: () => void } | null>(null);
  const townHostRef = useRef<HTMLDivElement | null>(null);
  const gameRef = useRef<Phaser.Game | null>(null);
  const sceneRef = useRef<TownScene | null>(null);
  const eventsRef = useRef<TerraEvent[]>([]);

  useEffect(() => {
    if (townHostRef.current && !gameRef.current) {
      const { game, scene } = createTown(townHostRef.current);
      gameRef.current = game; sceneRef.current = scene;
      // 初始空快照(只渲染固定班子)
      queueMicrotask(() => sceneRef.current?.applySnapshot(project([])));
    }
    return () => { subRef.current?.close(); gameRef.current?.destroy(true); };
  }, []);

  function pushEvent(e: TerraEvent) {
    eventsRef.current.push(e);
    setCount(eventsRef.current.length);
    sceneRef.current?.applySnapshot(project(eventsRef.current));
  }

  function connect() {
    subRef.current?.close();
    eventsRef.current = [];
    setCount(0);
    setPhase("catchup");
    setConnected(true);
    subRef.current = subscribe(base, session, {
      onEvent: (e, ph) => { setPhase(ph); pushEvent(e); },
      onCaughtUp: () => setPhase("live"),
      onClose: () => setConnected(false),
      onError: () => setConnected(false),
    });
  }

  return (
    <div style={{ padding: 16, fontFamily: "system-ui,sans-serif" }}>
      <h2 style={{ margin: "4px 0 12px" }}>
        TerraWorks · 像素小镇 <small style={{ color: "#888" }}>(M3-4 色块版)</small>
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
      <div ref={townHostRef} style={{ width: 880, height: 560, border: "1px solid #ddd" }} />
      <p style={{ fontSize: 12, color: "#777", marginTop: 8 }}>
        状态色:idle🟢 / decomposing🟡 / thinking🟣 / working🔵 / rework🔴 /
        awaiting_review🟠 / verifying🟪 / reviewing🟩 / hitl🔥 / error⚠️
        ｜ 描边色 = 职业(向导🟡 爆破🟠 裁缝🟢 appsec🟣 前端🔵…) ｜ 钟楼变亮 = merge 敲钟。
      </p>
    </div>
  );
}
