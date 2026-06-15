# Future Directions

记录 TerraWorks 当前阶段(M1.x)识别出来的、有意推后的设计方向。不是承诺,是路线候选——每条带启动 trigger,避免想法散失。

## GUI 角色编辑面板

让用户在 GUI 内编辑 `roles/*.md`(prompt + model + 工具白名单),降低非技术用户门槛。底层仍是文件,git diff 可追溯。

**启动 trigger**:M4 双向交互阶段。M4 本就要做点击 NPC 干预、HITL 敲玻璃等 GUI 交互,顺手把角色编辑面板加进去最经济。

## MCP 作为工具协议

把 `tools_allowed` 从 TerraWorks 自有工具(read/write/bash)扩展为 MCP server 接入。零代码扩展工具能力,生态对齐(同一个 GitHub MCP 既给 Claude Desktop 用、也给商人用)。

**启动 trigger**:M2 多 NPC + 审查闭环稳定后。Sandbox 基础设施在 M1.3-M2 跑顺、token 成本可控后,引入 MCP 适配层风险可承受。届时单独 ADR(候选 ADR-013)决议是否引入 + 协议层设计。

## NPC 真子进程化

ARCHITECTURE.md §10 终态设计("子进程 + git worktree 隔离"),M1.3 通过 ADR-012 推后到 M2。

**启动 trigger**:M2 第一次需要多 NPC 并发时。届时 `execute_npc` 包装为 subprocess + stdin/stdout JSON-RPC,接口签名不变;数据参数 JSON 化、基础设施句柄(session_store / llm_client)子进程端各自重建,写同一 SQLite WAL(并发时一并验证)。
