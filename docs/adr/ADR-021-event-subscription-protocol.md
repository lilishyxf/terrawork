# ADR-021: 事件订阅协议 + FastAPI 事件服务(M3-2)

## 背景

ADR-001:View = Session 订阅者,Catch-up(按 last_event_id 批量快进、不播动画)→ Live(WebSocket 逐条播)。§10:Harness = Python + FastAPI sidecar;事件推送走 WebSocket。

现状:harness 是库(advance/wake/SessionStore),**无事件推送 API**。M3-2 补一个**只读**事件服务,把 SQLite 事件流喂给 View。写路径(玩家交互→user_* 事件)是 M4,不在本步。

关键设计点:**Live 如何发现新事件?**
- 进程内 pub-sub:advance() 写事件时通知订阅者——但事件可能由**子进程**写入(ADR-017),进程内通知抓不到。
- **轮询 SQLite(选用)**:服务端按 cursor 周期性查"自 cursor 后的新事件"。DB 是唯一真相(事件溯源),轮询能捕获**任何进程**的写入,鲁棒。代价:~亚秒延迟(可调 poll_interval)。

## 决策

**FastAPI 只读事件服务 `harness/view/server.py`,`create_app(db_path, *, poll_interval=0.3)`。** 三个端点:

### 1. Catch-up(HTTP,游标分页)
`GET /sessions/{sid}/events?since=<int>&limit=<int>` →
```json
{"events": [<event>, ...], "cursor": <last_event_id 或 since>, "count": <n>}
```
- `since` 排他(`event_id > since`);`since=0` 取全部。游标语义对齐 query_session。
- 客户端循环拉取直到 `count < limit`(无更多页)。

### 2. Snapshot(HTTP,便利;复用参考投影)
`GET /sessions/{sid}/snapshot` → `{"snapshot": project(全部事件), "cursor": <last>}`。
- 复用 `harness/view/projection.project`(ADR-020)给出"当前小镇状态"快照,免客户端 replay。
- 与 ADR-001 不冲突:这是服务端用**同一份权威投影**做的便利优化;View 仍可选择自行 replay。

### 3. Live(WebSocket,Catch-up→Live 两阶段)
`WS /sessions/{sid}/live?since=<int>`,服务端帧:
```json
{"type": "event", "phase": "catchup", "event": {...}}   // 连接后先补发 since 之后的积压(快进,客户端不播动画)
{"type": "caught_up", "cursor": <int>}                  // 积压发完 → 切 Live
{"type": "event", "phase": "live", "event": {...}}      // 轮询到的新事件(客户端逐条播完整动画)
```
- 单条 WS 内完成 ADR-001 两阶段:`phase:"catchup"` 段快进定位,`caught_up` 后 `phase:"live"` 段播动画。
- Live 段轮询 SQLite(poll_interval),把自 cursor 后的新事件逐条推送、推进 cursor。
- 断开(WebSocketDisconnect)即结束循环,无副作用。

### 只读 + 无状态
- 服务**只读** SQLite,不写事件、不调 LLM、不驱动 advance(§1 红线#1:View 不写状态;玩家交互留 M4)。
- 每次查询开一个短命 SessionStore 连接(查完即关),避免跨线程共享 sqlite 连接;WAL 允许并发只读,轮询每 tick 见最新已提交。

## 不变量(INV,starlette TestClient 测试)

- **INV-1**:`GET /events?since=C` 只返 event_id>C、升序;`cursor` = 末条 id(空则原 since)。
- **INV-2**:`limit` 分页正确;循环拉取可完整取齐全序列。
- **INV-3**:`GET /snapshot` 返回 = `project(全部事件)`(与参考投影一致)。
- **INV-4**:WS 连接补发积压(phase:catchup)→ `caught_up` → 连接后新 append 的事件以 phase:live 推达,cursor 单调推进。
- **INV-5**:`since=C` 的 WS 不补发 ≤C 的事件(游标尊重)。

## 后果

- View(M3-3)可连上:catchup(HTTP 或 WS backlog)快进 + live 逐条播,落地 ADR-001。
- 轮询模型对子进程写入鲁棒(ADR-017);延迟由 poll_interval 调。
- 依赖新增 fastapi + uvicorn[standard](运行)+ httpx(测试 TestClient),记入 requirements。
- 仍只读;M4 加写端点(user_* 事件入 Session → 驱动 advance)时另起 ADR。
- M3-3 前端 ipc 客户端按本协议实现 catch-up→live 订阅。
