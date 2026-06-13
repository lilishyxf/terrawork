# Contracts 示例 Fixtures

> 用途：为 `docs/contracts/` 三个 schema 各提供最小完整正例，作为**设计说明**，帮助阅读 schema 与对齐字段语义。
> 性质：这是示例数据，不是测试脚本（测试将在 M1 阶段落到 `harness/tests/`）。所有示例均应能通过对应 schema 校验。
> 关联：[events.schema.json](events.schema.json)、[task_card.schema.json](task_card.schema.json)、[verification.schema.json](verification.schema.json)、ADR-004、ADR-006。

---

## 1. verification.schema.json

验证条件只能是两种形态之一（`oneOf` 互斥）。

### 1a. 最小 `machine_verifiable`（仅强制字段）

```json
{
  "type": "machine_verifiable",
  "command": "npm test -- auth",
  "expected": { "exit_code": 0 }
}
```

### 1b. 叠加可选断言的 `machine_verifiable`

```json
{
  "type": "machine_verifiable",
  "command": "pytest tests/test_login.py -q",
  "cwd": "harness",
  "timeout_sec": 120,
  "expected": {
    "exit_code": 0,
    "stdout_contains": "passed",
    "stderr_empty": true
  },
  "runner_role": "verifier",
  "test_provenance": "test_first_other_npc",
  "author_agent": "blaster#1"
}
```

### 1c. 最小 `hitl_escalation`（第三层兜底）

```json
{
  "type": "hitl_escalation",
  "reason": "登录页的视觉居中无可执行判据，无法表达为 exit_code 断言",
  "acceptance_prompt": "打开 /login，确认表单在视口中水平垂直居中后选择 approve/reject"
}
```

---

## 2. task_card.schema.json

强制四要素（`objective` / `output_format` / `allowed_tools` / `boundaries`）+ `verification`。

### 2a. 最小完整任务卡

```json
{
  "task_id": "t-login-impl",
  "objective": "实现邮箱+密码登录，让既有/先行编写的 auth 测试全部通过",
  "output_format": "修改 harness/.../auth 模块，导出 login(email, password) -> session_token",
  "allowed_tools": ["read", "write", "bash"],
  "boundaries": [
    "只改 auth 模块，勿动 db 迁移",
    "不得新增第三方依赖"
  ],
  "verification": [
    {
      "type": "machine_verifiable",
      "command": "npm test -- auth",
      "expected": { "exit_code": 0 }
    }
  ]
}
```

### 2b. 带分解/复杂度/依赖的任务卡

```json
{
  "task_id": "t-login-impl",
  "parent_task_id": "t-login",
  "assignee_role": "builder",
  "objective": "让 t-login-test 编写的登录测试全部通过",
  "output_format": "通过指定测试；不修改测试文件",
  "allowed_tools": ["read", "write", "bash"],
  "boundaries": ["只改 auth 模块", "禁止修改 tests/ 下文件"],
  "complexity": "complex",
  "think_required": true,
  "max_think_depth": 3,
  "depends_on": ["t-login-test"],
  "context_refs": [12, 18],
  "verification": [
    {
      "type": "machine_verifiable",
      "command": "npm test -- auth",
      "expected": { "exit_code": 0, "stdout_contains": "passed" },
      "test_provenance": "test_first_other_npc",
      "author_agent": "blaster#1"
    }
  ]
}
```

---

## 3. events.schema.json

每条事件 = 信封字段（`event_id`/`session_id`/`ts`/`agent`/`type`/`payload`，`parent_event_id` 表因果）+ 按 `type` 约束的 `payload`。

### 3a. 最小完整事件（`user_command`，因果链根）

```json
{
  "event_id": 1,
  "session_id": "s-2026-06-13-001",
  "ts": "2026-06-13T10:00:00Z",
  "agent": "user",
  "type": "user_command",
  "parent_event_id": null,
  "payload": { "text": "做个登录" }
}
```

### 3b. `guide_delegate`（内嵌完整任务卡，演示三 schema 嵌套）

```json
{
  "event_id": 7,
  "session_id": "s-2026-06-13-001",
  "ts": "2026-06-13T10:00:12Z",
  "agent": "guide",
  "type": "guide_delegate",
  "parent_event_id": 2,
  "payload": {
    "assignee": "merchant#1",
    "task_card": {
      "task_id": "t-login-impl",
      "objective": "实现邮箱+密码登录，让 auth 测试通过",
      "output_format": "修改 auth 模块，导出 login(email, password)",
      "allowed_tools": ["read", "write", "bash"],
      "boundaries": ["只改 auth 模块"],
      "verification": [
        { "type": "machine_verifiable", "command": "npm test -- auth", "expected": { "exit_code": 0 } }
      ]
    }
  }
}
```

### 3c. 预写配对：`tool_intent` → `tool_done`（`tool_done` 必带 `parent_event_id`）

```json
{
  "event_id": 20,
  "session_id": "s-2026-06-13-001",
  "ts": "2026-06-13T10:00:30Z",
  "agent": "merchant#1",
  "type": "tool_intent",
  "parent_event_id": 7,
  "payload": { "tool": "write_file", "params": { "file": "auth.ts", "content_len": 1280 }, "task_id": "t-login-impl" }
}
```

```json
{
  "event_id": 21,
  "session_id": "s-2026-06-13-001",
  "ts": "2026-06-13T10:00:31Z",
  "agent": "merchant#1",
  "type": "tool_done",
  "parent_event_id": 20,
  "payload": {
    "tool": "write_file",
    "status": "ok",
    "file": "auth.ts",
    "hash": "655048e5024cbb18c51e8e8a634ad334497f43cb94f9c7a08a44a9ba4d23950a"
  }
}
```

### 3d. 其余事件类型的最小 payload 速查

> 信封字段同上，此处仅列 `type` + 最小合法 `payload`。

| type | 最小 payload |
|---|---|
| `user_interact` | `{ "target": "merchant#1", "interaction": "inspect" }` |
| `guide_think` | `{ "text": "拆成 写测试→实现→验证 三步" }` |
| `npc_think` | `{ "text": "先读现有 auth 结构再动手", "task_id": "t-login-impl" }` |
| `review_request` | `{ "task_id": "t-login-impl", "reviewer": "tailor#1", "artifact": { "diff_ref": "wt-merchant-1@HEAD" } }` |
| `review_verdict` | `{ "task_id": "t-login-impl", "reviewer": "tailor#1", "verdict": "pass" }` |
| `verify_run` | `{ "task_id": "t-login-impl", "command": "npm test -- auth", "exit_code": 0, "passed": true }` |
| `merge` | `{ "task_id": "t-login-impl", "source": "wt-merchant-1", "target": "main", "result": "success", "milestone": true }` |
| `hitl_request` | `{ "reason": "视觉居中无机器判据", "question": "登录页是否居中?" }` |
| `hitl_response` | `{ "decision": "approve", "text": "居中没问题" }` （信封须带 `parent_event_id` 指回 hitl_request） |
| `error` | `{ "kind": "loop", "message": "同一测试连续 5 次失败，疑似循环", "task_id": "t-login-impl" }` |

> 备注：`guide_think` / `npc_think` 的 `payload` 结构相同（共用 `p_think`）。其可见性差异（对人透明、对审查 NPC 物理隔离，ADR-002）由 `assembleContext(role)` 代码强制，不体现在事件落盘结构里。
