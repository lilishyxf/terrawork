# State Reconstruction (M1.x)

> 本文件解释 M1.x 阶段"世界态"如何**从 Session 日志（prior chain）隐式还原**，而非作为独立对象持久化；并给出合法事件类型枚举与一张"业务直觉词 → 事件类型"易混淆词表。
>
> **唯一权威来源**：`docs/contracts/events.schema.json` 的 `type` enum。本文件是它的人读镜像——新增/修改事件类型一律**先改 schema**，本文件随后同步。任何与 schema 冲突之处，以 schema 为准。

## 1. 没有"世界态"对象

TerraWorks 不持有一个全局 `WorldState` / `world.json` 之类的可变对象。原因来自架构基线：

- **Session 日志是唯一事实源**（铁律④：仅追加、永不 UPDATE/DELETE）。
- **恢复状态、不恢复思维**（ARCHITECTURE §8）：磁盘上能全量重建的只有事件日志 + worktree 文件；任何"当前世界长什么样"的视图都是**从事件流当场算出来的派生量**，不单独存盘、不怕丢。

因此"世界态"= 一个**纯函数 `f(events) → view`**。每个消费者（executor、契约测试、未来的 `wake()`）各自遍历 Session、还原自己需要的那部分视图，而不是去读一个共享的世界态对象。

## 2. prior chain 如何隐式承载世界态

"prior chain" = 触发某次动作之前、Session 里已有的事件序列。它隐式编码了所有可还原的世界态。M1.3 的典型链：

```
user_command            玩家下达模糊指令
  └─ guide_think        Guide 推理（对人可见，对审查 NPC 隔离）
       └─ guide_delegate   产出任务卡（分解期，不绑 NPC 实例）
            └─ guide_assign 把某张任务卡派给具体实例 merchant#1（派发期）
                 ├─ tool_intent / tool_done   NPC 在 worktree 内执行（WAL 配对）
                 └─ review_request            执行结束信号
```

常见的"世界态查询"全部用 `query_session(...)` 遍历还原，无需新数据结构：

| 想知道的世界态 | 如何从事件流还原 |
|---|---|
| 哪些任务卡还没派发 | 找出 `guide_delegate.event_id` 未出现在任何 `guide_assign.payload.task_card_event_id` 里的（ADR-011） |
| 某张卡派给了谁 | 该卡的 `guide_assign.payload.assignee_instance`；重派时取**最新一条** `guide_assign`（旧的保留为历史） |
| 某 NPC 做到哪步 | 沿 `parent_event_id` 链收集该实例的 `tool_intent` / `tool_done` / `review_request` |
| 崩溃后哪步没完成（`wake()`，M1.4） | 找 `tool_intent` 无配对 `tool_done` 的 → 比对文件 hash → 重做或补记（第 4.3 节预写规则） |

**关键性质**：因为世界态是派生量，崩溃重启不需要"恢复内存对象"——只要日志在，重跑这些遍历即可得到一致视图。

## 3. 合法事件类型（当前 15 种）

以下为 `events.schema.json` 的 `type` enum，**当前共 15 种**（以 schema enum 为准；新增类型先改 schema，再回来同步本表）：

```
user_command      user_interact     guide_think
guide_delegate    npc_think         tool_intent
tool_done         review_request    review_verdict
verify_run        merge             hitl_request
hitl_response     error             guide_assign
```

> 计数沿革：v0.1 基础集 14 种（`user_command` … `error`）+ ADR-011 增补 `guide_assign` = 15。

## 4. 易混淆词表（业务直觉词 → 事件类型）

LLM 与开发者凭"业务直觉"造词时极易写出**不存在的事件类型**或**用错类型**，会被 schema 校验门当场拒（`SchemaError`）。下表是已踩过 / 易踩的坑：

| 你想表达 | ❌ 直觉常写 | ✅ 正确 | 说明 |
|---|---|---|---|
| 世界初始化 / 当前世界态 | `world_init` / `world_state` / `session_start` | （无此事件） | 世界态是派生量，从 prior chain 还原（见 §1/§2）。M1.3-4 起草 live 测试时真踩过：`append_event(type="world_state")` 被 schema 门拒——改用 `guide_delegate` + `guide_assign` 构造 prior。 |
| 把任务派给某 NPC | `guide_delegate` | `guide_assign` | `guide_delegate` = 产任务卡（分解期，不绑实例，ADR-010）；`guide_assign` = 绑到具体实例（派发期，ADR-011）。两者都有，别混。 |
| 工具调用的关联 id | `tool_call_id` 字段 | （无此字段；用 `parent_event_id` 链） | WAL 配对靠 `tool_done.parent_event_id == tool_intent.event_id`（M0 设计选择，ADR-007）。`tool_call_id` 只活在 LLM 协议的 messages 内部，**不落 Session**。 |
| 验证条件"转人工" | `hitl_request`（当成验证类型用） | `hitl_escalation` | `hitl_escalation` 是 **verification.schema.json** 里验证条件的一种类型；`hitl_request` 是 **events.schema.json** 里的事件类型。命中 escalation 路径时**才**落一条 `hitl_request` 事件。 |
| NPC 完成 / 收工 | `task_done` / `npc_done` | `review_request` | M1.3 中 NPC 执行结束的信号就是落一条 `review_request`，无独立的 "done" 事件。 |
| 跑测试 / 验证结果 | `test_result` / `verify_done` | `verify_run` | `verify_run` 由 verifier 产出（M1.4）。M1.3 商人自己 `bash` 跑测试是 `tool_done`（开发反馈），**不是** `verify_run`（权威验收）。 |
| 审查 | `review` / `review_result` | `review_request`（送审）/ `review_verdict`（结论） | 送审与结论是两个事件。 |
| 报错 / 卡死 / 循环 | `crash` / `stuck` / `loop` | `error`（`payload.kind ∈ {exception, stuck, loop, timeout}`） | 种类放 payload，不另立事件类型。 |
