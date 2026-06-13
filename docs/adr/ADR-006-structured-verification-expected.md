# ADR-006：验证条件 `expected` 字段结构化

> 状态：已接受 ｜ 日期：2026-06-13 ｜ 关联：ADR-004（验证条件三层方案）、ARCHITECTURE 第 6 节、铁律⑧
> 取代：ARCHITECTURE 第 6 节第一层中 `"expected": "exit_code == 0"` 的字符串示例

## 背景

ARCHITECTURE 第 6 节第一层要求验证条件"格式上强制可执行、不允许是自然语言判断"，但其示例把期望结果写成字符串字面量：

```json
{ "command": "npm test -- auth", "expected": "exit_code == 0", "type": "machine_verifiable" }
```

该字符串 `"exit_code == 0"` 是给人看的可读示例，本身并非机器可判定的结构——若按字面落地，验证执行器（爆破专家）必须**解析一段自然语言/伪代码谓词**才能判断通过与否。这与第一层的初衷自相矛盾：

- 谓词字符串是一个微型 DSL，需要解析器；解析器一旦出现，"太松/跑偏"的判断风险就从被禁止的自然语言悄悄回流。
- 不同写法（`exit_code==0`、`exit code is 0`、`returns 0`）都"看起来像"合法谓词，封闭性无法靠 JSON Schema 保证。
- 违背铁律⑧与 ADR-004 的核心主张："信任问题 → 执行问题"，验证 NPC 只执行不判断。

在 M1.0 撰写 `docs/contracts/verification.schema.json` 时，此矛盾必须先解决，否则 schema 无法在格式层强制可执行。

## 决策

将 `expected` 从字符串改为**结构化断言对象**，所有字段均为机器可直接判定，禁止自由文本谓词：

```json
{
  "type": "machine_verifiable",
  "command": "npm test -- auth",
  "expected": {
    "exit_code": 0
  }
}
```

`expected` 规则（详见 `verification.schema.json` 的 `machine_verifiable.expected`）：

- **`exit_code`（必填，integer）**：唯一强制断言，覆盖绝大多数 `命令 + 退出码` 验证。
- **可叠加的可选断言**：`stdout_contains`（子串精确匹配）、`stdout_matches`（正则）、`stderr_empty`（布尔）、`artifact`（产物路径 + `must_exist` + 可选 `sha256`）。
- `additionalProperties: false`：拒绝任何未定义字段，杜绝自然语言谓词从 `expected` 缝隙回流。

第二、三层不变：测试与实现分离（`test_provenance`）、不可验证则上抬 `hitl_escalation`。本 ADR 只强化第一层的"可执行"承诺。

## 理由

1. **真正实现"格式上强制可执行"**：执行器拿到 `expected` 后做的是字段比对（`actual.exit_code == expected.exit_code`），不需要任何谓词解析，不存在解释歧义。
2. **封闭性可被 JSON Schema 保证**：`additionalProperties: false` + 枚举/类型约束，使"什么是合法验证条件"在 schema 层即可判定，符合 ADR-004"靠形式约束而非更多审查"的思路。
3. **可扩展但不失控**：需要更强断言（输出匹配、产物校验）时，新增的是**结构化字段**而非放宽为自由文本——扩展方向天然指向可判定。
4. **与 verify_run 事件对齐**：`events.schema.json` 的 `verify_run` 记录实际 `exit_code` + `passed`（机器判定的布尔），与结构化 `expected` 一一对应，不再有"谁来解释字符串谓词"的灰区。

## 影响范围（哪些组件读取/产出该字段）

| 组件 | 读/产 | 责任 |
|---|---|---|
| `docs/contracts/verification.schema.json` | 定义 | `machine_verifiable.expected` 的结构化约束本体（本 ADR 落地处） |
| `docs/contracts/task_card.schema.json` | 引用 | 任务卡 `verification[]` 通过 `$ref` 内嵌该结构 |
| `harness/guide/`（向导/Guide） | **产出** | 生成验证条件时必须输出结构化 `expected`；禁止再生成字符串谓词，否则任务卡 schema 校验失败 |
| `harness/verify/`（验证执行器 / 爆破专家） | **读取** | 执行 `command` 后按 `expected` 各字段做机器比对，产出 `verify_run` 事件的 `passed` |
| `harness/context/`（assembleContext） | 读取 | 为 verifier 角色装配 context 时传递该 machine_verifiable 条件 |
| `harness/session/`（事件写入） | 间接 | `verify_run` 事件的 `exit_code`/`passed` 与 `expected` 对应，无需存 `expected` 原文 |
| View 层（爆破专家点引线动画） | 不读 `expected` | 只订阅 `verify_run` 的 `passed`/`exit_code`，不解析 `expected` |

## 后续

- ARCHITECTURE 第 6 节第一层示例同步更新为结构化形态（本 ADR 落地的一部分）。
- M1 阶段在 `harness/verify/` 实现执行器时，比对逻辑须覆盖 `expected` 的全部可选字段，并补 `harness/tests/` 用例（含本 ADR 的正/反例）。
