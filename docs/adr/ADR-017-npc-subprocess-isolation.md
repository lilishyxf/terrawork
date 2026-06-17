# ADR-017: NPC 执行子进程隔离（收尾 ADR-012）

## 背景

ADR-012 决策"M1.3 同进程函数调用、M2 升级真子进程，接口不变、实现替换"。ARCHITECTURE.md §10 技术栈终态写明"Agent 执行 = 子进程 + git worktree 隔离"。M2.6 A 半（ADR-016）已收尾文件系统隔离（worktree + 真 git merge）；本 ADR（B 半）收尾**进程隔离**——NPC 在独立子进程执行，崩溃不拖死 Harness、可独立资源限制、为 M2.5 真并行（绕 GIL 的并发 LLM/工具调用）铺地基。

recon 实情（重建面）：
- `SessionStore(db_path, *, session_id="default")` — 子进程端按 `db_path` + `session_id` 重建（写同一 SQLite，WAL 多写者）。
- `get_llm_client()` 读 `TERRA_LLM_MODE` 环境变量返回 mock/litellm 模块 — 子进程继承环境变量，调用 `get_llm_client()` 各自重建。
- `execute_npc(npc_instance_id, task_card, session_store, guide_assign_event_id, *, repo_root, worktrees_base, llm_client, scripted_actions, max_iterations, reuse_worktree, rework_notes)` — 数据参数皆 JSON-serializable；`session_store`/`llm_client` 是不可序列化的基础设施句柄（ADR-012 已分类）。

## 决策

### 1. `execute_npc` 保持进程内纯函数；新增 `subprocess_executor.py` 作子进程包装
- `execute_npc` 签名/语义不变，继续作为**离线确定性测试的执行体**与**子进程内核**共用。
- 新增 `harness/sandbox/subprocess_executor.py`：
  - **parent**：`run_npc_subprocess(*, db_path, session_id, ...所有数据参数...) -> None`，用 `subprocess.Popen([sys.executable, "-m", "harness.sandbox.subprocess_executor"])`，把全部数据参数 JSON 编码经 **stdin** 传入。
  - **child**：`main()` 读 stdin JSON → `SessionStore(db_path, session_id)` + `get_llm_client()` 重建基础设施句柄 → 调 `execute_npc(...)` → 成功 exit 0、失败把 traceback 写 stdout JSON 并 exit 1。

### 2. 退化版 JSON-RPC：一请求一响应 + 事件走 SQLite 旁路
- **请求**：parent → child，单个 JSON 对象经 stdin（instance_id / task_card / db_path / session_id / guide_assign_event_id / repo_root / worktrees_base / reuse_worktree / rework_notes / scripted_actions / max_iterations）。
- **响应**：child → parent，stdout 单行 JSON（`{"status":"ok"}` 或 `{"status":"error","error":"<traceback>"}`）+ 退出码（主信号）。
- **事件不走 IPC**：NPC 产出的 tool_intent/tool_done/review_request 由 child 端 SessionStore **直接写同一 SQLite 文件**（WAL 多写者），不经管道回传。匹配 ADR-012"子进程端各自重建 session_store 写同一 SQLite"。
- `llm_client` **不传**：child 一律 `get_llm_client()`（`TERRA_LLM_MODE` 环境变量继承）。scripted_actions 存在时 execute_npc 绕过 LLM，故子进程测试可用 scripted_actions 保持确定、无需传 stub。

### 3. 编排器 opt-in 开关（对 ADR-012"调用方代码不动"的细化）
- `advance(..., npc_in_subprocess: bool = False)`：**默认进程内**——保 64 个离线测试确定、快（不为每次 builder 派发 spawn 进程）；生产/演示 opt-in 子进程。
- 开关只切 **builder 执行**（execute_npc）的进程内/外；guide_step / verify_task / review_task 仍进程内（guide/tailor 的 LLM 与仲裁是 Harness 自身职责，非受隔离的 NPC sandbox）。
- **deviation 声明**：ADR-012 写"M2 调用方代码不动"。本 ADR 据测试确定性需要，让编排器多一个 `npc_in_subprocess` 开关。这是 ADR-012 的细化、非推翻——子进程内核仍是同一 `execute_npc`、签名不变。可追溯性由本 ADR + git diff 承载。

### 4. 崩溃隔离语义
- child 崩溃（未捕获异常 / `sys.exit(非0)` / 段错误）→ parent 检测到非零退出 → `run_npc_subprocess` 抛 `NpcSubprocessError`（含 child stdout 的 error 详情）。
- **Harness 主进程存活**：NPC 崩溃被隔离为一次子进程失败,而非拖死编排器。后续 `wake()` / `advance()` 据 Session 已落事件续作（与 M2.3 崩溃续作同源——已落的 tool_done 不丢，未完成的 build 由 finish_build 续）。
- 这正是 ADR-012 推迟、本 ADR 兑现的"真隔离好处"。

## 不变量（INV，B2 测试验证）

- **INV-1 subprocess_writes_to_shared_db**：子进程执行 builder 后，其 tool_intent/tool_done/review_request 出现在 parent 同一 SQLite（多写者 WAL 生效）。
- **INV-2 deterministic_via_scripted**：子进程 + scripted_actions 路径确定（无 LLM），产出事件与进程内路径同构。
- **INV-3 crash_isolated**：child 崩溃 → parent 收到非零退出并抛 NpcSubprocessError；主进程不崩；崩溃前已落的事件仍在 Session（可被 finish_build 续作）。
- **INV-4 orchestrator_subprocess_parity**：`advance(npc_in_subprocess=True)` 与默认进程内在同一 scripted 输入下产出等价终态（同一 task_board、同样 verified_pass / merge）。

## 后果

- **收尾 ADR-012 / §10 进程隔离终态**：NPC 在独立子进程跑，崩溃隔离、可独立资源约束、M2.5 真并行地基就位。ADR-012 至此 superseded（其分阶段计划完成）。
- **默认行为不变**：编排器默认仍进程内 → 现有 64 测试零改动、速度不退；子进程是 opt-in，由专门测试覆盖。
- **M2.5 衔接**：真并行时把 `npc_in_subprocess` 默认翻 True、并发 spawn 多个（受 `max_concurrent_agents` 约束，ADR-005）；本 ADR 不实现并发调度（仍顺序），只把单个 NPC 的执行隔离到子进程。
- **不改契约 schema**：事件类型/payload 不变；子进程只是 execute_npc 的执行宿主。
- **Windows 兼容**：用 `sys.executable -m harness.sandbox.subprocess_executor` + stdin JSON，不依赖 fork；跨平台。
