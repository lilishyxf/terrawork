import { describe, it, expect, vi, afterEach } from "vitest";
import { fetchEvents, subscribe, postCommand, postHitl, type TerraEvent, type Phase } from "./subscribe";

const ev = (id: number): TerraEvent => ({ event_id: id, type: "guide_think", agent: "guide", payload: {} });

afterEach(() => vi.restoreAllMocks());

describe("fetchEvents (HTTP catch-up 分页)", () => {
  it("游标分页拉齐全序列", async () => {
    // 两页:[1,2] then [3](count<limit 终止)
    const pages = [
      { events: [ev(1), ev(2)], cursor: 2, count: 2 },
      { events: [ev(3)], cursor: 3, count: 1 },
    ];
    const fetchMock = vi.fn(async (url: string) => {
      const since = Number(new URL(url).searchParams.get("since"));
      const body = since === 0 ? pages[0] : pages[1];
      return { ok: true, json: async () => body } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);

    const all = await fetchEvents("http://x", "s", 0, 2);
    expect(all.map((e) => e.event_id)).toEqual([1, 2, 3]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

// 最小 Fake WebSocket:同步驱动 onmessage,模拟服务端帧
class FakeWS {
  url: string;
  onmessage: ((m: MessageEvent) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;
  constructor(url: string) { this.url = url; }
  emit(frame: unknown) { this.onmessage?.({ data: JSON.stringify(frame) } as MessageEvent); }
  close() { this.closed = true; this.onclose?.(); }
}

describe("subscribe (WS 两阶段)", () => {
  it("catchup 帧 → caught_up → live 帧,分相位回调;http→ws URL 转换", () => {
    let ws!: FakeWS;
    vi.stubGlobal("WebSocket", vi.fn((url: string) => (ws = new FakeWS(url))) as unknown as typeof WebSocket);

    const seen: Array<[number, Phase]> = [];
    let caughtAt = -1;
    const sub = subscribe("http://h:8000", "sess", {
      onEvent: (e, phase) => seen.push([e.event_id, phase]),
      onCaughtUp: (c) => (caughtAt = c),
    }, { since: 0 });

    expect(ws.url).toBe("ws://h:8000/sessions/sess/live?since=0");

    ws.emit({ type: "event", phase: "catchup", event: ev(1) });
    ws.emit({ type: "event", phase: "catchup", event: ev(2) });
    ws.emit({ type: "caught_up", cursor: 2 });
    ws.emit({ type: "event", phase: "live", event: ev(3) });

    expect(seen).toEqual([[1, "catchup"], [2, "catchup"], [3, "live"]]);
    expect(caughtAt).toBe(2);

    sub.close();
    expect(ws.closed).toBe(true);
  });

  it("since 传入 WS URL", () => {
    let ws!: FakeWS;
    vi.stubGlobal("WebSocket", vi.fn((url: string) => (ws = new FakeWS(url))) as unknown as typeof WebSocket);
    subscribe("http://h:8000", "s", { onEvent: () => {} }, { since: 7 });
    expect(ws.url).toBe("ws://h:8000/sessions/s/live?since=7");
  });
});

describe("写半边 postCommand / postHitl", () => {
  it("postCommand POST /command 带 text,返回 event_id", async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      expect(url).toBe("http://h:8000/sessions/s/command");
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body as string)).toEqual({ text: "做个登录" });
      return { ok: true, json: async () => ({ event_id: 5 }) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    expect(await postCommand("http://h:8000", "s", "做个登录")).toBe(5);
  });

  it("postHitl POST /hitl 带 hitl_event_id/decision/text", async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      expect(url).toBe("http://h:8000/sessions/s/hitl");
      expect(JSON.parse(init.body as string)).toEqual(
        { hitl_event_id: 8, decision: "answer", text: "用 hashlib" });
      return { ok: true, json: async () => ({ event_id: 9 }) } as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    expect(await postHitl("http://h:8000", "s", 8, "answer", "用 hashlib")).toBe(9);
  });

  it("非 2xx 抛错", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 400 }) as Response));
    await expect(postCommand("http://h:8000", "s", "x")).rejects.toThrow("HTTP 400");
  });
});
