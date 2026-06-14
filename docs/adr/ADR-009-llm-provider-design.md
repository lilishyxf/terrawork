# ADR-009: LLM provider 接入设计与契约验收策略

## 背景

M1.2 是 TerraWorks 首次引入 LLM 调用的模块——Guide 分解委派由 LLM 驱动；随后 M1.3 的 NPC 执行、M2 的多 NPC 并发都依赖 LLM。设计层面的关键问题：接口是否绑死单一 provider、不同角色是否使用不同模型、契约 fixture 的验收是否要在多 provider 上保持一致性。

ARCHITECTURE.md §10 已明示"LLM 可插拔，角色级配置，不绑死供应商"；§9.1 角色文件 frontmatter 含 `model` 字段；§13 提到"Guide 用强模型、执行 NPC 用便宜模型的分级策略待验证"。本 ADR 把这条原则落到具体的工程实现与契约验收约束。

## 决策

1. **接口层用 LiteLLM 统一封装**。所有 LLM 调用通过 `litellm.completion(model=..., messages=..., ...)`，model 字符串作为唯一切换点。LiteLLM 内置支持 OpenAI / Anthropic / Google / DeepSeek / 通义 / 智谱等主流 provider，新增 provider 改一行配置即可。

2. **每个 NPC 角色文件（`roles/*.md`）通过 frontmatter 的 `model` 字段独立指定 LLM**。Guide 同样以角色文件形式存在（`roles/guide.md`）——它即 §9.2 角色表中的 orchestrator 角色，其 LLM 配置同样落在角色文件，而非散落于 Harness 代码。无全局默认 model；未声明 model 的角色文件视为非法。

3. **`roles/guide.md` 的 `model` 字段初始设为 `deepseek/deepseek-chat`**。理由：成本低、中文 prompt 友好、便于多轮迭代测试；后续验收质量不足时改该字段升 Claude/GPT，无需改代码。

4. **M1.2b 契约验收必须在三家 provider 上全部通过**：`deepseek/deepseek-chat`、`openai/gpt-4o`、`anthropic/claude-sonnet-4-6`。同一组 invariants 在三家上全部成立才算契约通过。三家选定覆盖国产 / OpenAI / Anthropic 三类风格差异显著的 provider，provider 多样性证据充分。

5. **API key 通过环境变量加载**：`DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`，本地通过 `.env` 文件配置，`.env` 加入 `.gitignore` 禁止入库（本 ADR 落地时一并补）。

## 后果

- M1.2 代码层面只需写一个 `litellm.completion` 调用 + 配置加载，不为每家 provider 写适配器。
- 角色文件成为"prompt + 模型配置"的单一来源，符合 ADR-005 "角色即插件"精神。
- 契约 fixture 的 INV-7 直接编码 provider 列表（见 `m12_login_command.json`），验收时遍历执行。
- 执行 NPC（商人、爆破专家）的 model 由各自角色文件指定；具体"强 vs 廉价分级策略"推荐留到 M1.3 落地时单独 ADR。
- 新增 provider 到契约只需：(a) 注册 API key 到环境变量；(b) 把 model 字符串加进 INV-7 test_providers 数组；(c) 重跑契约。无代码改动。
- 本 ADR 不覆盖 LLM 调用的可观测性（token 计数、延迟、重试）。该议题留待 M1.4 验证条件执行器实现时一并讨论。
