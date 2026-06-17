# M1.4 Acceptance: Verification Executor + End-to-End Loop (closes M1)

**Status**: Accepted on 2026-06-17
**Scope**: M1.4-1 → M1.4-5b (commits eaf7426 → <this commit>)

M1.4 闭合 M1 全环:**一条指令 → 自动分解 → 执行 → 验证 → 验收 → 日志可回放 + 干净机器 wake 重建**(ARCHITECTURE §12 M1 验收)。

## Sub-milestones

| ID | Commit | Deliverable |
|---|---|---|
| M1.4-1 | eaf7426 | verify+verdict 契约 fixture(m14) + ADR-014 + INV-1~7 自洽 |
| M1.4-2 | 260c56d | blaster 角色 + verify_executor(确定性) + e2e(INV-8 runtime) |
| (cleanup) | 0b4db31 | INV-8 写语义澄清 + verify_run/review_verdict 补 ADR-007 双约束 |
| M1.4-3 | 45f5b75 | decide_verdict(确定性) + m14b fail 路径契约 |
| M1.4-4 | dae527c | wake(sessionId) 崩溃恢复(最小版,§8) |
| M1.4-5a | 76ef322 | orchestrator(事件驱动 advance) + 离线确定性 wiring e2e |
| M1.4-5b | <this commit> | live 全链(真实 DeepSeek) + 干净机器 wake 演示 + 本文档 |

## 全环组件

| 环节 | 组件 | 性质 |
|---|---|---|
| 分解委派 | `guide.step.guide_step` | LLM(DeepSeek) |
| 派发执行 | `sandbox.executor.execute_npc`(builder) | LLM(迭代 tool calling) |
| 验证 | `sandbox.verify_executor.verify_task`(blaster) | **确定性**(subprocess + 字段比对,不调 LLM) |
| 验收 | `guide.verdict.decide_verdict` | **确定性**(机器判定权威,不调 LLM) |
| 崩溃恢复 | `wake` | **确定性**(纯读重建) |
| 编排 | `orchestrator.advance` | 事件驱动循环到静止 |

## Invariant 状态

| INV(m14/m14b/m14c) | 含义 | scripted/offline | live DeepSeek |
|---|---|---|---|
| m14 INV-1~7 | verify+verdict pass 路径契约 | ✓ | ✓(全链) |
| m14 INV-8 | verifier 在 builder worktree 只读+执行 | ✓(e2e) | ✓ |
| m14b INV-1~6 | fail 路径(reject + hitl 兜底)+ 机器判定权威 | ✓ | — |
| m14c | wake 任务板重建 + unpaired 检测 | ✓ | ✓(干净机器 wake) |

## DeepSeek Live 指标(M1.4-5b 实测)

- Model: deepseek/deepseek-chat(via LiteLLM)
- 指令(决策 (i) 收窄为原子任务):"实现 login.py 单文件…只产一张卡…verification 用 python -c …"
- **guide 产卡数: 1**(单卡流成立)
- 完整事件链(22 事件):`user_command → guide_think → guide_delegate → guide_assign(merchant#1) → 7×(tool_intent/tool_done) → review_request → guide_assign(blaster#1) → verify_run → review_verdict`
- **builder 工具调用: 7 次**(真实迭代 tool calling)
- verify_run.passed = **True**;review_verdict = **pass**(机器判定权威成立)
- 干净机器 wake:换全新 SessionStore 打开同一 session.db → task_board = `{t-...: verified_pass}`、unpaired = []、无报错
- 全链耗时: **~24-27s**
- token 用量: 当前 facade 不单独暴露,见 live 测试日志

## Known Limitations

- **单卡流(决策 B)**:M1 orchestrator 只驱动单 builder 卡;多 builder 卡会显式抛 `NotImplementedError`(共享 merchant#1 worktree)。多卡 test-first 编排(卡间依赖、verifier-role 卡)**留 M2**。
- **live 用收窄指令(决策 (i))**:M1.4-5b live 验收用原子任务强 steer 单卡,证的是"**全环在真实 LLM 上跑通**";"**模糊指令 → LLM 自由多卡 test-first 自动拆解**"的深化**留 M2**(多卡编排)。这是有意的范围收窄,非掩盖。
- **reject 路径无自动返工**:fail → review_verdict(reject) + hitl_request 上抬人工(option b);多轮返工循环留 M2。
- **verifier 读 builder worktree**(ADR-014):M1 只读+执行直跑 builder worktree;真隔离(merge-then-verify / 独立验证 worktree)留 M2。
- **单实例**:ROLE_INSTANCE = builder→merchant#1 / verifier→blaster#1;多实例并发留 M2。
- **wake 不 re-dispatch**:§8 步骤 4(in-progress 任务自动重派,需 LLM)留 M1.4-后续/M2;当前 wake 纯读重建视图 + 给 reconcile|redo 建议,不自动执行。
- **bash denylist 非 OS 沙箱**(ADR-014 候选);OS 级隔离留 M2。

## M1 闭环声明

M1（无头 Harness）目标达成:Session 日志(append-only + WAL + schema 校验门) + Guide 分解委派 + 单 NPC 执行 + 验证条件三层 + 验收 + 崩溃恢复,在真实 DeepSeek 上端到端跑通一次,日志完整可回放、干净机器 wake 状态完全重建。

## Next: M2 — 多 NPC + 审查闭环

- 多卡 test-first 编排(卡间依赖)+ 模糊指令自由分解
- 裁缝(tailor)代码审查(context 物理隔离 think)+ 退回重做循环
- 多实例并发 + NPC 真子进程化(ADR-012)+ merge-then-verify 真隔离
- 验收:强杀进程重启后无缝续作(wake re-dispatch)
