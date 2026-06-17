# ADR-012: NPC 执行的同进程函数调用，M2 升级真子进程

> **状态**：分阶段计划已完成，由 **ADR-017**（M2.6-B 子进程隔离）收尾。本 ADR 的 M1.3 同进程阶段仍有效（`execute_npc` 进程内纯函数保留为默认与子进程内核）。

## 背景

ARCHITECTURE.md §10 技术栈表写明 "Agent 执行 = 子进程 + git worktree 隔离"，§13 第 4 条提到 "10 NPC 并行的成本上限需在 M2 实测"。这是面向 M2 多 NPC 并发场景的设计。

M1.3 起步只有**单 NPC 单实例**（商人 merchant#1）执行。真子进程化的工程代价（IPC 协议设计、stdin/stdout JSON-RPC、跨进程错误处理、序列化约束）在此阶段不带来对应收益——M1.3 无并发、商人崩溃等价于 Harness 崩溃（反正都要 `wake(sessionId)`）。

强制 M1.3 走子进程化会让本阶段范围爆炸，违反 §12 "每周交付一个能 demo 的能力，不堆叠半成品"原则。注：子进程隔离是 §10 终态技术栈选型，不属于铁律⑦"循环五部件"（自动化触发器 + 独立 worktree + 磁盘记忆 + MCP 连接器 + 制作/检查子代理分离）；worktree 隔离本 ADR 保留（见下方签名 `worktree_path`），故推迟子进程化不违反铁律⑦。

## 决策

**分阶段实现 NPC 执行**：

1. **M1.3 阶段**：NPC 执行函数 `execute_npc(npc_instance_id, task_card, worktree_path, session_store, llm_client) -> None` 在 Harness 主进程内**同步调用**。所有 tool_intent/tool_done 事件直接写 Session，不跨进程；商人 LLM 调用走 LiteLLM 同步接口。

2. **M2 阶段**：同样的函数签名包装为 `subprocess.Popen + stdin/stdout JSON-RPC`，实现真隔离。**接口不变，实现替换**——M1.3 阶段的调用方代码在 M2 不需要修改。

为保证 M2 平滑升级，参数分两类、跨进程边界待遇不同：

- **数据参数**（`npc_instance_id: str`、`task_card: dict`、`worktree_path: str`）：必须 JSON-serializable，M2 经 stdin/stdout JSON-RPC 跨进程传递。不传 Python-only 对象（callable、open file handle 等）。
- **基础设施句柄**（`session_store`、`llm_client`）：**不跨进程传递**。`session_store` 持有 sqlite 连接、`llm_client` 是 module 引用，二者皆不可序列化。M1.3 同进程直接传引用；M2 子进程端按 db 路径 / 角色配置**各自重建**自己的 session_store 与 llm_client（写同一个 SQLite 文件，WAL 多写者，M2 并发时一并验证）。

此外，`execute_npc` 须遵守：

- **不返回值**：产出全部通过 session_store 写事件传递（与 Guide 同款约定）
- **不持有跨调用状态**：函数体内变量在调用结束后释放；商人"记忆"全在 Session 里（纯函数原则，与 ADR-009 一致）

## 后果

- M1.3 范围降低约 40-50%（无 IPC 协议工作）
- M2 升级时新增 `harness/sandbox/subprocess_executor.py` 作为 `execute_npc` 的子进程包装，主调用方代码不动；子进程端按数据参数 + db 路径/角色配置重建基础设施句柄
- 真隔离的好处（NPC 崩溃不拖死 Harness、独立资源限制）推后到 M2 多 NPC 并发时一并到位
- M1.3 阶段商人 crash 拖死 Harness 是已知代价——单 NPC 单实例，crash 等价会话失败，`wake()` 一次即可，不阻塞
- `docs/future-directions.md` 中"M2 NPC 真子进程化"列为已锁定的下一阶段技术债（M1.3 启动时一并落盘）
- 本 ADR 是路线选择，不修订任何契约文件；ARCHITECTURE.md §10/§13 不修改——§10 写的是终态，§12 里程碑表本来就是分阶段实施
