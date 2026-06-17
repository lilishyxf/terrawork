# ADR-014: M1 阶段 verifier 在 builder worktree 内只读+执行验证命令

## 背景

ARCHITECTURE.md §1 职责红线 #3 明示："Sandbox 之间不共享可写文件系统。跨 NPC 的产物交换只通过 git merge 走 Guide 仲裁。"

M1.4 引入 verifier（爆破专家）执行 `task_card.verification[].machine_verifiable.command` 验证 builder（商人）的产物。验证天然需要访问 builder 的工作产物——`python -c "from login import login; assert ..."` 必须能 import 到商人在 worktree 内写下的 `login.py`。

严格按红线 #3 的做法是：M1.4 同时引入 git merge 仲裁（把商人分支 merge 到一个"验证 worktree"，verifier 在那跑）。但 §12 把 merge 排在 M2"多 NPC + 审查闭环"里，且 M1.3-3 的 review_request → M1.4 verify → review_verdict 这条链路本就是 M1 单 NPC 验收闭环的最小可行版本——M1.4 阶段强引入 merge 会让范围爆炸，同 ADR-012 拒绝在 M1.3 强引入子进程化的判断同款。

需要明确：M1.4 verifier 如何访问 builder 产物，且该让步的边界是什么。

## 决策

**M1.4（单 NPC 单实例）阶段，allow verifier 直接在 builder 的 worktree 内只读 + 执行验证命令**：

- verifier 的 `cwd = data/worktrees/merchant-1`（builder 的 worktree 根，或 verification.cwd 指定的子目录）
- verifier **只跑**`task_card.verification[].machine_verifiable.command`（命令本身可能读 builder 产物，如 `python -c "from login import ..."`）
- verifier **不写**任何文件到 builder worktree（不允许 verifier 通过 tool_intent 触发 write/bash-with-redirect 修改 builder 产物）
- verifier 产出 `verify_run` 事件（如实记录 exit_code + passed + output_summary），仅此而已

红线 #3 的让步明确限定：**只读+执行、非可写共享**。verifier 不创建/修改 builder worktree 内文件。

## 后果

- **M1.4 实施代价**：verify_executor 函数签名 `verify_task(verifier_instance_id, task_card, builder_worktree, session_store)`，无 merge 协议、无独立验证 worktree 创建，范围 controllable。
- **M2 升级路径锁定**：M2 引入"merge-then-verify"——Guide 把 builder 分支 merge 到 verification worktree（或临时 detached HEAD），verifier 在验证 worktree 跑。届时 verify_executor 的签名不变（依然接 `builder_worktree`），但 `builder_worktree` 由 Guide merge 出来，与 builder 实际 worktree 分离。**接口不变、实现替换**，同 ADR-012 节奏。
- **"只读+执行不写"的强制方式**：M1.4 verify_executor **不暴露 tool_intent 接口**——verifier 只能执行 verification.schema 里那条 command，不像 builder 那样有 read/write/bash 工具白名单。这从 API 层挡掉了"verifier 顺手改文件"的可能。规则上由 ADR 明示、机制上由 executor 不开放写 API 共同保证。
- **真隔离推后**：M2 多 NPC 并发时，多个 verifier 共用同一 builder worktree 会冲突，必须升 merge-then-verify。M1.4 单 NPC 单实例下二者无并发，让步安全。
- **§12 M1 验收成立**：M1 验收要求"一条模糊指令 → 自动拆解 → 执行 → 验证 → 日志完整可回放"，本 ADR 让 M1.4 在不引入 merge 的前提下达成验证那一环。
- **本 ADR 修订基线职责红线 #3 的解释（M1 阶段限定）**：可追溯性由本 ADR + git diff 承载，不在 ARCHITECTURE.md §1 加标注（沿用 ADR-007/010/011 同款治理）。M2 引入 merge-then-verify 时另起 ADR 收尾该让步。
