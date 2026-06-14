# ADR-010: guide_delegate 事件的 assignee 字段放宽为可选

## 背景

M0 阶段 `docs/contracts/events.schema.json` 中 `p_guide_delegate` 的 payload required `["assignee", "task_card"]`，其中 `assignee` 语义为"接收任务的 NPC 实例 ID"。此约束隐含假设"分解任务"与"派发到具体 NPC 实例"是同一动作。

M1.2 起草契约 fixture 时识别出该假设与本阶段设计存在模型冲突：

- M1.2 的范围是 Guide 的**分解委派**，产出是合规的任务卡，任务卡躺在 Session 日志中等待后续派发。
- 派发到具体 NPC 实例（如 `assignee = merchant#1`）是 M1.3 Sandbox 接收任务时由 Guide 重新决定的——届时根据 max_concurrent_agents、各 NPC 当前忙闲、role 匹配做派发决策。

强制 M1.2 阶段就填 `assignee` 会迫使任务卡绑死实例 ID，违反 §9 "role 是契约、实例是派发决策"原则，也与职责红线 #2「Harness 不持有任何不可重建的内存状态」及 §8「恢复状态不恢复思维」不一致——实例 ID 属派发期决策，不应固化进分解期产物。

## 决策

修订 `docs/contracts/events.schema.json` 中 `p_guide_delegate` 的 required 字段：

- **修订前**：`required: ["assignee", "task_card"]`
- **修订后**：`required: ["task_card"]`；`assignee` 保留为可选字段，允许 M1.3 派发阶段填入。

`task_card.assignee_role`（由 task_card.schema.json 强制 required）承载"角色契约"——M1.2 产出的任务卡必须声明需要的 role，但不指定具体实例 ID。

既有 EXAMPLES.md / fixture 中带 assignee 的 guide_delegate 示例**保留不动**——assignee 变可选后它们仍合规，且正好演示可选字段的存在形态；仅 M1.2 新 fixture（`m12_login_command.json`）不含 assignee。

## 后果

- M1.2 产出的 guide_delegate **不含** assignee；派发到具体 NPC 实例由 M1.3 完成。因 Session **仅追加**（铁律④，UPDATE 被触发器拒），**不对既有 guide_delegate 事件补写字段**，而是**新增一条事件**记录"哪张 task_card 派给哪个 NPC 实例"（候选事件类型 `guide_assign`，具体留 M1.3 设计时定）。
- M1.2 fixture（`m12_login_command.json`）的 guide_delegate 事件不含 `assignee`，通过修订后的 schema 校验。
- 现有 M0 fixture（login_chain.json、invalid_events.json）需重跑校验确认仍合规——`assignee` 从 required 变 optional 是**放宽**，既有合规数据不可能被新规则拒绝，但要跑一遍坐实（等价 ADR-007 落地时做的回归）。
- 本修订是基线契约的放宽而非收紧，不会让任何既有合规事件变成不合规。
- ADR-007 修订 events.schema.json 时未在 ARCHITECTURE.md §3 加标注，本 ADR-010 亦不加。ADR-008 治理规则中"修改基线 ADR 加标注"约束的是 ADR 文件本身的修改，不约束 `docs/contracts/*.schema.json` 这类契约文件的修订——后者的可追溯性由 git diff + 独立 ADR 自身承载。
