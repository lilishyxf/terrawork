# ADR-016: 真 git-merge 产物交换 + per-card worktree 隔离（收尾红线 #6）

## 背景

ARCHITECTURE.md §1 职责红线 #6 / PROJECT.md 禁止做的 #6 原文：

> **Sandbox 之间不共享可写文件系统；跨 NPC 的产物交换只通过 git merge 走 Guide 仲裁。**

这是终态。M1/M2 为控制范围、不让里程碑爆炸，临时让步了三次，每次都用 ADR 记成"待 M2.6 收尾"：

- **ADR-012**：NPC 执行是同进程函数调用（非子进程）。← B 半收尾，另见 ADR-017。
- **ADR-014**：verifier 直接进 builder 的 worktree 只读+执行（非 merge-then-verify）。
- **ADR-015**：同一 feature 的多 builder 实例**共享一个 worktree**（feature-1）交换产物（非 git merge）。

recon 还暴露一个事实：**当前系统里没有任何真实 git commit / merge**——builder 用 `write` 工具改文件但从不提交，产物是未提交工作区改动；`merge` 事件是纯日志记录（payload 写了 `source`/`target` 但不跑 git，`commit` 字段从不填）。

本 ADR（M2.6 A 半）收尾 ADR-014 + ADR-015 的文件系统让步，引入系统第一笔真实 git 提交与合并。

## 决策

**产物只以 git 提交存在，跨 NPC 交换只走真 git merge，每张卡独立 worktree。**

### 1. per-card / per-instance worktree（撤销 ADR-015 共享）
- 撤销 `_FEATURE_WORKTREE_KEY` 共享模型；恢复 per-instance worktree：`merchant#1 → data/worktrees/merchant-1`、`merchant#2 → merchant-2`，各关联分支 `npc/merchant-1`、`npc/merchant-2`（worktree.py 既有 `branch_name`）。
- M2.4 中卡↔实例为 1:1（每张 builder 卡分到不同实例，返工同卡同实例），故 per-instance worktree 即 per-card worktree。
- **无两个实例共享可写 worktree 路径**——直接关掉红线 #6 的共享让步。

### 2. builder 完工即 commit（产物=已提交分支态）
- `execute_npc` 在构建循环结束、落 `review_request` 之前，对 worktree 执行 `git add -A && git commit`（提交信息含 instance + task_id）。
- commit 在 `execute_npc` 内（fork 决策）：产物即"已提交分支态"，merge 干净；B 半子进程化后子进程端 commit 也自然（子进程有 worktree 的 git 访问）。
- 返工（rework）在同 worktree 复用、追加新 commit，分支前进。
- 空改动（builder 没写任何文件）允许 `--allow-empty` 提交，保证分支恒有一个可 merge 的 commit（链路不因空产物断裂）。

### 3. 依赖交付靠 "merge-to-main + branch-from-main"（无需显式 merge 依赖分支）
- `depends_on` 门已保证：依赖卡（测试卡）**先验证通过、merge 到 main**，编排器才派依赖它的卡（实现卡）。
- 故实现卡的 worktree 只要**从 main 切**（`create_worktree(..., base_branch="main")`，既有缺省），就自带测试卡已 merge 进 main 的产物——**实现者由此看见测试**，取代 ADR-015 共享目录。
- 简化结论：不需要"把 `npc/merchant-1` 显式 merge 进 merchant-2 worktree"；branch-from-main 天然继承。

### 4. merge-then-verify（撤销 ADR-014 直接进 builder worktree）
- verifier 不再在 builder 的 live worktree 跑。改为 Guide 把 builder 分支签出到一个**独立验证 worktree**：`git worktree add --detach <verify-path> npc/merchant-N`（detached，避开"分支已在别处签出"限制；得到该分支产物的隔离签出）。
- verifier（`verify_task`）的 `builder_worktree` 参数改接**这个验证 worktree**——签名不变、实现替换（ADR-014 已锁定此升级路径）。
- 验证完销毁验证 worktree（`git worktree remove --force`）。
- 实现卡的验证 worktree 含测试（来自 main）+ 实现（来自 npc/merchant-2 commit），故 `pytest test_login.py` 能跑通——隔离签出仍见全部所需产物。

### 5. 仲裁 pass → 真 git merge 到 main
- `arbitrate_pass` 把记录性 merge 换成真 merge：在 repo_root（main 工作树）跑 `git merge --no-ff npc/merchant-N`，把真实提交 hash 填进 `merge.payload.commit`。
- 冲突时 `result: "conflict"`（p_merge schema 已支持），落事件后转 HITL 兜底（不自动猜解冲突）。M2.4 顺序执行 + 依赖排序下，单 session 内卡间冲突预期罕见；冲突路径仅作兜底，不实现自动消解。
- main HEAD 随每次 pass 真实前进、累积各卡产物。

### 6. 不改契约 schema
- `events.schema.json` 的 `p_merge` 已含 `source`/`target`/`result(success|conflict)`/可选 `commit`——M2.6 只是**首次真实填充** `commit` 并真跑 git，无 schema 变更。
- `task_card.schema.json` / `verification.schema.json` 不变。

## 不变量（INV，A3 e2e 验证）

- **INV-1 isolated_worktree**：两张 builder 卡在**不同** worktree 目录（merchant-1 ≠ merchant-2），无共享可写路径。**取代 ADR-015 / m2d fixture 的 INV-7 shared_worktree。**
- **INV-2 builder_commits**：每个 builder 分支在构建后含真实 commit（分支 ref 前进；产物可被 merge）。
- **INV-3 dep_via_main**：实现卡 worktree 从 main 切、且在测试卡 merge-to-main 之后创建，故含测试文件——无需共享目录即见依赖产物。
- **INV-4 merge_then_verify**：verifier 运行目录是独立验证 worktree（≠ builder live worktree），且仍能跑通（验证签出含全部所需产物）。
- **INV-5 real_merge_to_main**：`arbitrate_pass` 后 main HEAD 真实前进、含被合并产物；merge 事件 `result="success"` 且 `commit` 为真实 hash（`^[0-9a-f]{7,40}$`）。
- **INV-6 redline6_closed**：全流程结束，任意两个 NPC 实例的 worktree 路径互不相同；跨卡产物只经 git（main）流动，无 sandbox 间可写共享。
- **INV-7 multicard_e2e_still_green**：现有多卡 test-first 闭环（分解→执行→验证→审查→merge）在真 merge 模型下仍走通，两卡终态 verified_pass。

## 后果

- **闭合偏差 #2 / 红线 #6**：跨 NPC 产物交换从"共享目录 + 记录性 merge"升级为"独立 worktree + 真 git merge 经 Guide 仲裁"。ADR-014（verifier 跨读）、ADR-015（共享 worktree）的文件系统让步**至此收尾、被本 ADR superseded**。
- **`wake` 的 hash 比对回退修复**：ADR-015 曾记"agent 产物在 feature-1 而非 merchant-1 → wake hash 比对偏 redo"。本 ADR 恢复 per-instance worktree，`instance_to_slug(agent)` 重新对齐产物位置，该回退随之消除。
- **测试基线变动**：`test_m2_multicard_e2e` 的 INV-7（断言单一 feature-1 目录）反转为 INV-1（断言 merchant-1/merchant-2 两个独立目录）；`test_m2_rework` 的 worktree 路径断言由 feature-1 改回 merchant-1。验证命令仍用 stdlib import（impl 验证签出含测试+实现两文件 → 验 merge-then-verify 拿到全部产物）。
- **首次引入 git 副作用**：worktree.py 新增 `commit_worktree` / `merge_to_main` / `add_detached_worktree` helper（A2）；orchestrator/executor/verify_executor 接真 merge（A3）。
- **B 半（子进程 ADR-012）不受影响**：另起 ADR-017，A 半完成后做。
- **可追溯性**由本 ADR + git diff 承载；不改 ARCHITECTURE.md 正文（§1 红线终态本就如此，本 ADR 是让其落地、非偏离）。
