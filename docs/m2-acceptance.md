# M2 Acceptance (partial): 审查闭环 + 退回重做 + 崩溃续作

**Status**: M2.1 / M2.2 / M2.3 accepted on 2026-06-17 (commits c677710 → <this commit>)
**Scope so far**: 多 NPC 审查闭环 + 退回重做循环 + 崩溃重启无缝续作（§12 M2 验收已达成）。
剩余 M2.4 多卡 / M2.5 多实例 / M2.6 子进程+merge-then-verify 见末尾。

## Sub-milestones

| ID | Commit | Deliverable |
|---|---|---|
| M2.1 (隔离) | c677710 | assembleContext 物理隔离 + tailor 角色 + 隔离 INV（铁律③/§5/ADR-002） |
| M2.1 (审查步) | 24ee666 | review_task：tailor 出 review_verdict（decision 甲，机器判定权威代码强制） |
| M2.2a | 5571451 | orchestrator 接 tailor review + Guide merge 仲裁（verify+review 两道闸） |
| M2.2b | 2d95cdc | 退回重做循环（reject→re-dispatch builder，≤max_rework + 复用 worktree → 超限 hitl） |
| M2.3 | <this commit> | 崩溃重启无缝续作（finish_build + advance 幂等续作）→ **§12 M2 验收** |

## 审查闭环（M2.1/M2.2）

- **物理隔离**（铁律③/§5/ADR-002）：审查 NPC 的 context 由 `filter_events_for_review` 代码硬过滤，剥离 `guide_think`/`npc_think`（叙述），只留事实（tool_intent/done、verify_run、review_verdict）。隔离 INV：泄露哨兵字符串**绝不**出现在审查 context。
- **两道独立闸**（decision 甲）：
  - `verify_run`（blaster，机器测试）— "能不能跑通"
  - `review_verdict`（tailor，代码审查，看隔离事实）— "写得对不对"
  - **机器判定权威（INV-5）代码强制凌驾 LLM**：verify 失败 → reject 且跳过 LLM；无通过 verify_run → 即使 LLM 说 pass 也降级 reject。
- **Guide 仲裁**：review pass → `merge` 事件（终态；M1 单 worktree 记录性 merge，真 git merge 留 M2.6）。

## 退回重做（M2.2b）

- review reject → 重派 builder 返工（复用同一 worktree，注入 reject notes），≤ `max_rework`（默认 2）次。
- 超限 → `hitl_request` 兜底（不无限循环；终止性由 round-aware _decide + reject_count 上界保证）。
- orchestrator `_decide` 改为 **round-aware**（`_has_later` "无更晚配对事件"），支持同一 task 多轮 verify→review。

## 崩溃续作（M2.3 — §12 M2 验收）

- **approach B**：`advance` 事件驱动 + 幂等,**重启续作 = reopen 同一 session.db 后再 advance()**。
- 各崩溃点续作能力：分解后/验证中途/审查中途/仲裁前 → advance 自然续作；**构建中途**（builder 已派、无 review_request）→ `finish_build` 阶段续作（按 worktree 是否已建决定 reuse/create）。
- wake 保持**纯读诊断**（M1.4-4），不承担 re-dispatch（职责分离）。
- 验收实测（test_m2_resume）：进程1 跑到"构建中途"持久化后强杀（builder 已派 + worktree 已建 + 一条 unpaired tool_intent）→ 进程2 reopen db + advance → 续作补出 review_request→verify→review(pass)→merge,任务板重建为 verified_pass;append-only 下崩溃前事件无丢失。

## 测试

- 离线全套件 **55 passed**（M1 全量 + M2.1~2.3）；live 路径 skipif-gated（M2.2a 全链 live、tailor review live 已验）。
- M2 关键测试:隔离(2) + 审查步(4) + 编排全链(2) + 退回重做(3) + 崩溃续作(2)。

## Known Limitations / 剩余 M2

- **单卡流**：orchestrator 仍单 builder 卡（多 builder 卡显式 NotImplementedError）。多卡 test-first 编排（卡间依赖 + 模糊指令自由分解）→ **M2.4**。
- **单实例**：ROLE_INSTANCE 各 1 实例（merchant#1/blaster#1/tailor#1）。多实例并发 + max_concurrent_agents（ADR-005）→ **M2.5**。
- **记录性 merge / verifier 直跑 builder worktree**（ADR-014）：真 git merge + 子进程真隔离（ADR-012）+ merge-then-verify → **M2.6**。
- **rework_notes 注入**仅 LLM 路径（scripted 测试不走）；多轮返工的 builder 是否真"看到并改正"需 live 验证（留 M2.4+ 或专项）。
