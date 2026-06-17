# ADR-018: 并行多实例派发（M2.5 真并行）

## 背景

ARCHITECTURE.md §12 M2 范围含"并行 worktree"；§13.4"token 成本：10 NPC 并行的成本上限需在 M2 实测"；ADR-005 定 `max_concurrent_agents=10`（可调）。M2.6（ADR-016 文件系统隔离 + ADR-017 进程隔离）已备好真并行的地基——独立 worktree + 独立分支 + 子进程执行 + 多写者 SQLite。

M2.4 起编排器是**顺序**的：`advance` 每轮 `_decide` 返回**一个**可行动状态、阻塞执行、再循环。多卡（M2.4）也是顺序派发（受 `depends_on` 排序）。真并行此前缺地基（ADR-012 同进程 + GIL + 阻塞 execute_npc 无法真并发），M2.6 补齐后本 ADR 落地。

**关键约束**：真并行只对**互不依赖的卡**有意义。同一 feature 的 test-first 卡是顺序的（impl `depends_on` tests），无并行机会；并行发生在**多张 ready（依赖已满足）且未派发的 builder 卡**之间。

## 决策

**给 `advance` 加一条并行快路：当 ≥2 张 builder 卡同时 ready（依赖满足、未派发），并发执行它们；否则走现有顺序路径。** 不重写为全并发调度器（避免跨阶段竞争复杂度）。

### 1. ready 收集
- 新增 `_ready_builder_dispatches(events)`：返回所有满足"无引用它的 builder guide_assign（未派）+ `_deps_satisfied`（depends_on 全已 merge）"的 builder delegate（即 `_decide` stage 2 的判据，但收集**全部**而非返回第一个）。

### 2. 并行批（≥2 张 ready）
顺序、可控的三步准备 + 并发执行：
1. **顺序写各自 `guide_assign`**（每张卡按 `_builder_instance` 定实例 merchant#N；写事件快，串行无害）。
2. **顺序预建各 worktree**（`create_worktree` 从 main 切）——**规避 `git worktree add` 的并发锁竞争**（git 对 `.git/worktrees` + index 持锁，并发 add 会失败）。预建后子进程 `reuse_worktree=True`。
3. **`ThreadPoolExecutor(max_workers=max_concurrent_agents)`** 并发提交 `run_npc_subprocess(reuse=True)`，等全部完成。每线程阻塞在各自子进程（释放 GIL）→ 子进程真并行。

### 3. 并行批恒走子进程
- 并行批**总是**用子进程（ADR-017），与单卡的 `npc_in_subprocess` 开关**解耦**：进程内（GIL + 阻塞）无法真并行,"并行"即意味着子进程。
- 单卡（仅 1 张 ready）仍走现有顺序 `_decide`，尊重 `npc_in_subprocess` 开关（默认进程内）。

### 4. 其余阶段保持顺序
- verify / review / arbitrate（merge）仍每轮一个、顺序处理。
- **`merge_to_main` 串行**：在 repo_root（main 工作树）逐个 merge，无并发 race。互不依赖卡改不同文件，正常无冲突；万一冲突 → `result:conflict` + HITL 兜底（ADR-016 已有）。

### 5. max_concurrent_agents
- `advance(..., max_concurrent_agents: int = 10)`（ADR-005 默认）。ready 卡数 > 上限时，线程池排队（不超并发上限）。

## 并发安全论证
- **worktree/分支/事件互不相交**：merchant-1/merchant-2 独立目录、npc/merchant-1|2 独立分支、各自 tool_intent/tool_done 事件链。
- **main 只读**：并行 build 期间不 merge，仅各自从 main 切时读 main。
- **SQLite 多写者**：N 个子进程并发 `append_event` 由 WAL 写锁（`BEGIN IMMEDIATE`）串行化——写快、不阻塞并行的 LLM/工具工作。这兑现 ADR-012"M2 并发时一并验证多写者 SQLite"。

## 不变量（INV）

data-scope（M2.5-1 fixture + 自洽 test）：
- **INV-1 two_independent_cards**：分解出恰好 2 张 builder 卡，**互不依赖**（均无 depends_on，或不互相依赖）。
- **INV-2 independent_verification**：每卡 verification 仅验自身产物（互不引用对方）。
- **INV-3 cards_pass_schema**：两卡均过 task_card.schema。

runtime-scope（M2.5-2 e2e）：
- **INV-4 concurrent_batch_dispatch**：两卡在同一并行批派发（两个 builder guide_assign 在任一卡的 review_request 之前成对出现）。
- **INV-5 both_merge**：两卡都 verified_pass + 真 merge 到 main。
- **INV-6 actual_parallelism**：计时证据——各 builder 含 ~1s 阻塞工作时，墙钟 < 顺序耗时（< 1.7s vs 顺序 ≥2s），证子进程真并发。
- **INV-7 max_concurrent_respected**：同时活跃子进程数不超 `max_concurrent_agents`。

## 后果

- **M2 完整收官**：§12"并行 worktree"真正落地；M2 五大件（多 NPC / 审查闭环 / 返工 / 续作 / 多卡 test-first + 并行）全部到位。
- **多写者 SQLite 实测**：并发子进程写同一库的正确性在 INV 下验证，收尾 ADR-012/§13.4 的并发验证遗留。
- **成本说明**（§13.4）：`max_concurrent_agents` 默认 10 为并发上限；真实 token 成本随并发线性增长，由用户按预算调小。本 ADR 不改默认值。
- **不改契约 schema**：事件/任务卡 schema 不变；并行是编排器的派发策略。
- **顺序路径不变**：单卡、test-first 顺序多卡（depends_on）行为不变；并行仅在 ≥2 张独立卡 ready 时触发。
- **M3 衔接**：并行执行使"一屏看多个 NPC 同时干活"的 View（M3）有真实并发状态可渲染。
