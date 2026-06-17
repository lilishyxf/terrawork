---
name: blaster
display_name: 爆破专家
role: verifier
domain: engineering
specialty: verification
model: null  # M1.4 verifier 不调 LLM (确定性 subprocess 执行 + 字段比对)
tools: []  # ADR-014: verifier 不暴露 read/write/bash 工具白名单 (API 层不暴露, 文档层呼应)
max_think_depth: 0  # 确定性验证, 无 think
sprite: blaster.png
idle_behavior: 检修引线
---

# Blaster — Verifier (爆破专家)

爆破专家是 M1.4 引入的验证角色。它读取 task_card 的 `verification[]`,在 builder 的
worktree 内执行其中的命令,把结果如实落成 `verify_run` 事件(机器可验证条件)或 `hitl_request`
事件(无法机器验证、上抬人工)。M1.4 阶段它是**纯确定性**的——`verify_run.passed` 完全等价于
"命令 exit_code + expected 字段的布尔比对",**不调用 LLM**,链路上不出现任何模型推理。
M2 可能扩展(例如验证失败时用 LLM 起草更友好的 `hitl_request.question`),但 M1.4 不引入。

## 原则

- **只执行不判断**(ADR-004):信任问题转化为执行问题。爆破专家只跑 `verification[]` 里给定的
  命令、只做结构化字段比对,不对"代码好不好"做主观判断。命令逐字执行,不自创、不改写。
- **只读+执行,不写**(ADR-014):在 builder worktree 内运行命令(可读其产物),但绝不修改
  worktree 内任何文件。机制上由 verify_executor 不暴露 read/write/bash 工具白名单保证。

## 边界声明

M1.4 阶段 verifier **不参与 `review_verdict` 的产生**——verdict 由 Guide 仲裁路径基于
`verify_run` 推出(判定权在编排者、执行权在验证者,职责分离)。爆破专家的产出仅限
`verify_run` / `hitl_request`。
