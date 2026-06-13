# ADR-007：parent_event_id 对 tool_done / hitl_response 约束升级为非空正整数

> 状态：已接受 ｜ 日期：2026-06-13 ｜ 关联：ARCHITECTURE 第 4.3 节（预写规则）、events.schema.json、M1.1 验收
> 影响文件：docs/contracts/events.schema.json

## 背景

ARCHITECTURE 第 4.3 节确立预写规则：所有改变世界的动作"先写意图事件 → 执行 → 再写完成事件"，且 `tool_done` 必须经因果链指回其配对的 `tool_intent`（`hitl_response` 同理须指回 `hitl_request`）。为落实这一点，M0 阶段的 `events.schema.json` 在对应 `if/then` 分支里用 `"required": ["parent_event_id"]` 来要求这两类事件携带父事件。

M1.1 实施 Session 写入门时发现：JSON Schema 的 `required` 只校验**键是否存在**，并不校验其值。由于 Session 层组装事件时 `parent_event_id` 这个键**恒存在**（无父事件时取值 `null`），`required` 形同虚设——一条 `parent_event_id: null` 的 `tool_done` 会被判合规放行。这与第 4.3 节"预写配对"的本意直接冲突：无配对 intent 的 done 事件本应在写入门就被拦下，否则崩溃恢复时的"intent 无配对 done"扫描逻辑会被污染（出现"done 无配对 intent"的非法状态）。

## 决策

将这两个分支对 `parent_event_id` 的约束从"仅 `required`"升级为在类型层强制非空正整数：

```json
"then": {
  "properties": {
    "payload": { "$ref": "#/$defs/p_tool_done" },
    "parent_event_id": { "type": "integer", "minimum": 1 }
  },
  "required": ["parent_event_id"]
}
```

`type: "integer"` 排除 `null`（信封顶层 `parent_event_id` 本身允许 `["integer","null"]`，此处在 `then` 中收窄），`minimum: 1` 对齐 `event_id` 自增从 1 起的事实，`required` 保留以拦"键缺失"。三者叠加后，`tool_done` / `hitl_response` 必须携带一个指向真实父事件的正整数 id 方能落盘。`hitl_response` 分支同步采用相同写法。

## 后果

- **已用 9 个回归 case 验证当前 schema 仍然自洽**——包含 schema 自身的 meta-schema 合法性自检、ADR-007 约束在 `tool_done` 与 `hitl_response` 两个分支均生效的对偶回归、以及 EXAMPLES.md 渲染出的 login_chain 完整事件链。其中"缺 parent 应被拒"一例在本次升级前会误判通过、升级后正确拒绝，反向印证修正有效；`hitl_response` 分支的对偶回归则证明该约束不是只在一处生效。后续任何新增的、依赖父子关系的事件类型，直接复用 `{"type":"integer","minimum":1}` 约束模式（配合 `required` 拦键缺失），不再单用 `required`。
  - (2026-06-13 补)上述 9 个回归 case 已在 Python 3.11.5 .venv 内重跑全部 PASS;同一组 case 在 3.11 与 3.13 上行为完全一致(拒绝集合相同、错误信息相同),确认 ADR-007 决策在不同 Python 版本上稳定生效。
- 该修正仅收紧校验、不改变任何合法事件的形态，属对既有意图的强化而非设计偏离；不触及 ARCHITECTURE.md 正文。
