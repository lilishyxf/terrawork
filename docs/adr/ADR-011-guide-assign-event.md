# ADR-011: 新增 guide_assign 事件类型

## 背景

ADR-010 把 `p_guide_delegate.required` 从 `["assignee", "task_card"]` 放宽为 `["task_card"]`，把"派发到具体 NPC 实例"推后到 M1.3 处理。后果段明确"由 M1.3 产生新的 `guide_assign` 事件类型记录'哪张 task_card 派给哪个 NPC 实例'，此细节留待 M1.3 设计时定"。

M1.3 起草时需要明确具体实现：**走"新增 guide_assign 事件"路径**，而非"给既有 guide_delegate 追加字段"——后者违反铁律④，与 M1.1 已落地的 `BEFORE UPDATE … RAISE(ABORT)` 触发器直接冲突。

## 决策

在 `docs/contracts/events.schema.json` 新增事件类型 `guide_assign`：

- **type**：`guide_assign`
- **agent**：必须为 `guide`（只有编排者能派发）
- **parent_event_id**：必须为正整数（继承 ADR-007 约束），指向被派发的 `guide_delegate` 事件的 `event_id`
- **payload required**：`["task_card_event_id", "assignee_instance"]`
- **payload 字段**：
  - `task_card_event_id`（integer ≥ 1）：被派发的 guide_delegate event_id。与 parent_event_id 在因果链上重复，但在**业务语义**上明确——parent_event_id 是事件溯源，task_card_event_id 是"派的是哪张卡"
  - `assignee_instance`（string，pattern `^[a-z_][a-z0-9_]*#[0-9]+$`）：NPC 实例 ID，格式 `<role_name>#<instance_number>`，如 `merchant#1`、`tailor#2`

**agent 字段不修订**：经核对 events.schema.json 现状，`agent` 为 `{type: string, minLength: 1}`、无 pattern 约束（可容纳 `merchant#1` 这类实例 ID），故本 ADR 不修订 agent 字段。

**落盘机制（三处编辑，缺一不自洽）**：新增一个事件类型在 events.schema.json 内需同时改三处——

1. 顶层 `type` enum 追加 `"guide_assign"`（13 项 → 14 项）；
2. `$defs` 新增 `p_guide_assign`：`additionalProperties: false`、`required: ["task_card_event_id", "assignee_instance"]`、两字段按上文类型/pattern 约束；
3. `allOf` 新增 if/then 分支，且**照搬 ADR-007 在 tool_done 上的双约束写法**——`then` 内 `"properties": { "payload": {"$ref": "#/$defs/p_guide_assign"}, "parent_event_id": {"type": "integer", "minimum": 1} }` + `"required": ["parent_event_id"]`。**不可只写 required**：JSON Schema 的 required 只校验键存在、不拦 `null`，必须叠加 `type: integer` 才能真正强制非空正整数（这正是 ADR-007 的教训）。

## 后果

- M1.3 派发逻辑：Guide 从 Session 里读出未派发的 guide_delegate 任务卡 → 为每张生成 guide_assign 事件 → Sandbox 按 guide_assign 启动对应 NPC 实例
- "未派发"判定：遍历 Session，找出 guide_delegate.event_id 没出现在任何 guide_assign.payload.task_card_event_id 里的——M1.3 用既有 `query_session` 实现，无需新工具
- 重试派发场景（NPC 实例崩溃）：产生**新的** guide_assign 指向同一 task_card_event_id 但不同 assignee_instance；旧 guide_assign 保留作为历史，最新一条生效——append-only 友好
- **task_card_event_id 与 parent_event_id 当前恒等**：按本设计两者都指向同一 guide_delegate，包括重试场景。保留独立字段是为 M2 留余地——届时"重试 assign"的 parent_event_id 可改指向触发重派的 crash/error 事件（因果上更准），而 task_card_event_id 仍稳定指向那张卡，二者解耦后才各司其职。
- **文档计数同步**：events.schema.json 内有两处硬写"13 种事件类型"（顶层 description 与 type enum 注释），落地时同步改为"14 种"（或注明"v0.1 最小集 13 种 + ADR-011 增补 guide_assign"）。ARCHITECTURE.md §4.2 标题为"事件类型（v0.1 最小集）"，作为 v0.1 最小集的历史快照**不动**——guide_assign 是 M1.3 增量，由本 ADR + git diff 承载可追溯。
- 现有 fixture 不受影响：M0 的 login_chain.json、M1.2 的 m12_login_command.json 都不含 guide_assign（那是 M1.3 才有的事件），仍合规
- 本 ADR 修订 docs/contracts/events.schema.json，可追溯性由 git diff + 本 ADR 承载，不在 ARCHITECTURE.md §3 加标注（沿用 ADR-007/010 同款治理）
