// M3-3 ipc 客户端:按 ADR-021 订阅 Session 事件流(catch-up → live)。
// View 只读(§1 红线#1):仅消费事件,不写状态。

export interface TerraEvent {
  event_id: number;
  type: string;
  agent: string;
  payload: Record<string, unknown>;
  parent_event_id?: number | null;
  session_id?: string;
  ts?: string;
}

export type Phase = "catchup" | "live";

export interface SubscribeHandlers {
  onEvent: (event: TerraEvent, phase: Phase) => void;
  onCaughtUp?: (cursor: number) => void;
  onError?: (err: unknown) => void;
  onClose?: () => void;
}

export interface Subscription {
  close: () => void;
}

/** HTTP catch-up:游标分页拉齐 since 之后的全部事件(ADR-021 /events)。 */
export async function fetchEvents(
  baseUrl: string,
  sessionId: string,
  since = 0,
  limit = 500,
): Promise<TerraEvent[]> {
  let cursor = since;
  const all: TerraEvent[] = [];
  for (;;) {
    const url = `${baseUrl}/sessions/${sessionId}/events?since=${cursor}&limit=${limit}`;
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`catch-up failed: HTTP ${resp.status}`);
    const body = (await resp.json()) as { events: TerraEvent[]; cursor: number; count: number };
    all.push(...body.events);
    cursor = body.cursor;
    if (body.count < limit) break; // 无更多页
  }
  return all;
}

/** WebSocket 两阶段订阅:补发积压(phase:catchup)→ caught_up → 实时(phase:live)。 */
export function subscribe(
  baseUrl: string,
  sessionId: string,
  handlers: SubscribeHandlers,
  opts: { since?: number } = {},
): Subscription {
  const since = opts.since ?? 0;
  const wsBase = baseUrl.replace(/^http/, "ws");
  const url = `${wsBase}/sessions/${sessionId}/live?since=${since}`;
  const ws = new WebSocket(url);

  ws.onmessage = (msg: MessageEvent) => {
    let frame: { type: string; phase?: Phase; event?: TerraEvent; cursor?: number };
    try {
      frame = JSON.parse(typeof msg.data === "string" ? msg.data : "");
    } catch (err) {
      handlers.onError?.(err);
      return;
    }
    if (frame.type === "event" && frame.event) {
      handlers.onEvent(frame.event, frame.phase ?? "live");
    } else if (frame.type === "caught_up") {
      handlers.onCaughtUp?.(frame.cursor ?? 0);
    }
  };
  ws.onerror = (e) => handlers.onError?.(e);
  ws.onclose = () => handlers.onClose?.();

  return { close: () => ws.close() };
}
