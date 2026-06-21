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

/** 写半边(ADR-022):下指令。POST /command → 后台 advance 驱动,结果经 WS 流回。 */
export async function postCommand(
  baseUrl: string, sessionId: string, text: string, model?: string,
  attachments?: { name: string; content: string }[],
): Promise<number> {
  const r = await fetch(`${baseUrl}/sessions/${sessionId}/command`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, model: model || null, attachments: attachments?.length ? attachments : null }),
  });
  if (!r.ok) throw new Error(`command failed: HTTP ${r.status}`);
  return (await r.json()).event_id as number;
}

/** 写半边(ADR-022):回应某个 HITL 卡口。decision: answer(带整改指引 text)| reject(放弃)。 */
export async function postHitl(
  baseUrl: string, sessionId: string,
  hitlEventId: number, decision: "answer" | "reject", text?: string,
): Promise<number> {
  const r = await fetch(`${baseUrl}/sessions/${sessionId}/hitl`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hitl_event_id: hitlEventId, decision, text }),
  });
  if (!r.ok) throw new Error(`hitl failed: HTTP ${r.status}`);
  return (await r.json()).event_id as number;
}

/** 工作区:列文件(相对路径)。 */
export async function fetchWorkspaceTree(baseUrl: string): Promise<string[]> {
  const r = await fetch(`${baseUrl}/workspace/tree`);
  if (!r.ok) throw new Error(`tree failed: HTTP ${r.status}`);
  return (await r.json()).files as string[];
}

/** 工作区:读单个文件内容。 */
export async function fetchWorkspaceFile(baseUrl: string, path: string): Promise<string> {
  const r = await fetch(`${baseUrl}/workspace/file?path=${encodeURIComponent(path)}`);
  if (!r.ok) throw new Error(`file failed: HTTP ${r.status}`);
  return (await r.json()).content as string;
}

/** 工作区:当前目标仓库路径。 */
export async function getWorkspace(baseUrl: string): Promise<{ path: string; is_git: boolean }> {
  const r = await fetch(`${baseUrl}/workspace`);
  if (!r.ok) throw new Error(`workspace failed: HTTP ${r.status}`);
  return await r.json();
}

/** 工作区:切到目标项目仓库(NPC 在此改代码、merge 进 main)。 */
export async function setWorkspace(baseUrl: string, path: string): Promise<{ path: string }> {
  const r = await fetch(`${baseUrl}/workspace`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
  return await r.json();
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
