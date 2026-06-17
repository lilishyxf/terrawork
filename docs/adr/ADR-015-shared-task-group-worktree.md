# ADR-015: 按 task-group 共享 worktree（落地 test-first 的测试作者≠实现者）

> **状态**：M2.4 阶段让步，已由 **ADR-016**（M2.6-A 真 git-merge + per-card worktree）superseded——产物改经真 git merge 经 main 流动、每卡独立 worktree，共享目录模型撤销（wake hash 比对回退随之修复）。

## 背景

ADR-004 第二层 / 铁律⑧的 test-first 要求:**写测试的 NPC ≠ 写实现的 NPC**(防"实现自己说自己对"),且制作 NPC 的任务是"让已存在的测试通过"。这意味着:

- 测试卡派给一个实例(如 merchant#1)、实现卡派给**另一个**实例(merchant#2)——否则测试作者=实现者,违反分离。
- 但实现者必须**看得见**测试(对着测试写实现、verification 跑测试)。

当前 worktree 模型是 **per-instance**(merchant#1 → data/worktrees/merchant-1):两个不同实例各自独立 worktree,merchant#2 看不见 merchant#1 写的测试。于是 test-first 落不了地——这是 M2 偏差分析里"离设想最远"的 #3。

严格按 §1 红线#3 的解法是 merge 仲裁(把测试卡产物 merge 进实现卡 worktree),但那是 M2.6;真并行多实例是 M2.5。M2.4 不想为落地 test-first 就立刻吞下 M2.5+M2.6。

## 决策

**M2.4 引入"按 task-group 共享 worktree":一个 feature(一组经 `depends_on` 关联的任务卡)共用一个 worktree。**

- worktree 由 `worktree_key`(如 `feature#1`,仍满足实例 slug 正则 `^[a-z_][a-z0-9_]*#[0-9]+$` → `data/worktrees/feature-1`)标识,而非按 NPC 实例。
- 同一 feature 的不同 builder 实例(merchant#1 写测试、merchant#2 写实现)在**同一共享 worktree** 内工作——实现者因此看得见测试。
- **顺序、非并行**:实现卡 `depends_on` 测试卡,编排器在测试卡 merge 后才派实现卡。无并发写同一 worktree → 无冲突(真并行留 M2.5)。
- **同 worktree、非 git-merge**:产物共享靠同目录,不引入 merge-then-verify(留 M2.6)。

实现要点:
- `execute_npc` 增 `worktree_key`(缺省 None → 用 npc_instance_id,**向后兼容**);编排器对一个 feature 的所有 builder/verify 传同一 `worktree_key`。
- 实例分配:builder 卡按出现顺序分配 `merchant#1`、`merchant#2`…→ 保证测试作者≠实现者。
- 单卡流是 N=1 特例(一个 builder 卡、无 deps、共享 worktree),行为不变(仅 worktree 目录名由 `merchant-1` 变为 `feature-1`)。

## 后果

- **闭合偏差 #3**:test-first(测试作者≠实现者 + 实现者看见测试)真正落地;live demo 可用真·模糊指令(不再 option-i 收窄单卡)。
- **与 §1 红线#3 的关系**:本 ADR 把"同一 feature/信任域内、顺序"的 worktree 共享显式合法化——比 ADR-014(verifier 跨读 builder worktree)更进一步的共享。真隔离(每实例独立 worktree + git-merge 仲裁交换产物)仍是终态,留 M2.6 收尾红线。M2.4 的共享限定在"同 feature、顺序、同信任域",无并发越权。
- **wake 的 unpaired-intent hash 比对**按 `instance_to_slug(agent)` 定位 worktree;改 group 后 agent(merchant#1)的产物在 `feature-1` 而非 `merchant-1`,故 hash 比对会偏向 redo(找不到文件)。这是诊断精度的已知回退,不影响任务板重建(M2.4 记为已知限制;wake 的 group 感知留后续小改)。
- 真并行多实例(M2.5)、真 git-merge 隔离(M2.6)不受影响,各自仍待做。
- 本 ADR 修订 worktree 模型(harness 内部),可追溯性由 git diff + 本 ADR 承载;不改 ARCHITECTURE.md 正文(§1 红线终态不变,M2.6 收尾)。
