# 角色源文件归档（role-sources）

这些是 **agency-agents**（https://github.com/msitarzewski/agency-agents）的原始提示词，作为
TerraWorks 角色库的**能力参考**收存于此。它们**不是** TerraWorks 角色文件，不被 harness 加载
（`load_role_frontmatter` 只读 `roles/<name>.md`）。

## 为什么不直接用
原始提示词面向"独立 Claude Code 子代理"，与 TerraWorks 体系冲突：
- frontmatter 是 `name/color/emoji/vibe`，缺 `role/specialty/model/tools/sprite`（双轴模型，ADR-019）。
- 通篇 "you remember…" 的长期记忆 → 违反 PROJECT.md 禁止 #8（禁跨任务 NPC 记忆）。
- "Step 1…N / deliverable template" 步骤模板 → 违反铁律⑥（教原则不教步骤）。
- 不认任务卡契约（objective/boundaries/verification）、read/write/bash 工具、完成信号、maker≠checker。

故 M2.7-2 **蒸馏重写**为 `roles/*.md`（principle-based、套契约、去记忆与步骤、保专业内核）。

## 适配映射
| 源文件 | → roles/ | 处置 |
|---|---|---|
| engineering-frontend-developer | `frontend.md` | builder |
| engineering-backend-architect | `backend.md` | builder |
| engineering-database-optimizer | `database.md` | builder |
| engineering-ai-engineer | `ai_engineer.md` | builder |
| engineering-rapid-prototyper | `rapid_proto.md` | builder |
| engineering-technical-writer | `tech_writer.md` | builder |
| engineering-mobile-app-builder | `mobile.md` | builder |
| security-appsec-engineer | `appsec.md` | reviewer |
| engineering-code-reviewer | 并入 `tailor.md` | reviewer（增强，不另立角色） |
| design-ui-designer | —（待 M4） | 设计/HITL，M4 HITL 通道就绪后再适配 |
| gis-cartography-designer | —（待 M4） | 设计/HITL，同上 |

> `desktop_shell.md` 无对应源文件，M2.7-2 从零原创（Tauri/Electron 桌面壳，对齐 TerraWorks 自身技术栈）。
