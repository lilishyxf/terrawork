# 项目名：TerraWorks

> 本文件为 ARCHITECTURE.md 的工程化提炼（速查 + 红线）。一切冲突以 ARCHITECTURE.md 为准；需偏离基线先提 ADR 到 docs/adr/ 再动代码。

## 这是什么

TerraWorks 是一个**游戏化的多 Agent 编排平台**：像素小镇是操作界面而非装饰——跟向导对话即下任务、点 NPC 即干预、一屏世界即系统全量健康状态。它与同类产品（Pixel Agents / AgentRoom）的本质区别在于**双向编排**（动画 ⇄ Harness 操作闭环），而非 trace→动画的单向被动可视化。游戏化是信息密度手段，不是产品定位。

## 技术栈

（按 ARCHITECTURE.md 第 10 节）

| 层 | 选型 | 理由 |
|---|---|---|
| 桌面壳 | Tauri 2.0 | 体积/内存优势；alwaysOnTop 等窗口控制；Rust 后端对事件流友好 |
| 游戏渲染 | Phaser 3（WebView 内） | 成熟 2D 像素引擎；可参考 Pixel Agents 开源实现 |
| 前端框架 | React 18 + TypeScript | 非游戏 UI（详情面板、设置、任务板） |
| Harness | Python 3.11 + FastAPI（sidecar） | LLM 编排生态最全、经验可复用 |
| Session 存储 | SQLite（WAL 模式） | 单文件、内建预写日志、零运维 |
| LLM | 可插拔（DeepSeek / Claude / 任意 OpenAI 兼容） | 角色级配置，不绑死供应商 |
| Agent 执行 | 子进程 + git worktree 隔离 | 铁律⑦；轻于容器、够用 |
| 事件推送 | WebSocket（Harness → View） | Catch-up + Live 双阶段协议 |

## 目录约定

（按 ARCHITECTURE.md 第 11 节）

```
terraworks/
├── src-tauri/                # Tauri 壳（窗口、托盘、sidecar 启动）
├── src/                      # 前端
│   ├── game/                 # Phaser 场景、精灵、动画状态机
│   │   └── protocol/         # 状态-动画映射表（第 7 节的代码化）
│   ├── panels/               # React 面板（任务板、详情、设置）
│   └── ipc/                  # 事件订阅客户端（catch-up + live）
├── harness/                  # Python sidecar
│   ├── guide/                # 编排核心：分解、委派、验收、仲裁
│   ├── session/              # 日志读写、WAL、查询工具
│   ├── context/              # assembleContext + 可见性矩阵
│   ├── verify/               # 验证条件 schema 与执行器
│   ├── sandbox/              # worktree 生命周期管理
│   └── tests/
├── roles/                    # 角色定义（用户可扩展）
├── docs/
│   ├── ARCHITECTURE.md       # 设计基线
│   ├── adr/                  # 决策记录，新决策追加编号
│   └── contracts/            # 事件 schema、任务卡 schema、验证条件 schema
└── data/                     # session.db、worktrees（gitignored）
```

## 规则

### 必须遵守（从 8 条铁律 + 5 条 ADR 提炼）

1. **三层 + View 解耦**：View 只读 Session，禁止直接写状态；玩家交互一律转成 `user_*` 事件追加进 Session，由 Harness 消费（CQRS 单向数据流，铁律①）。
2. **Harness 无状态**：不持有任何不可重建的内存状态；任何重要决策**先落日志再生效**，崩溃后 `wake(sessionId)` 从日志续作（铁律①④⑧）。
3. **Session 仅追加**：事件表只 INSERT，禁止 UPDATE/DELETE；"压缩/清理/归档"一律生成新文件，原始日志不动（铁律④）。
4. **预写规则（Write-Ahead）**：所有改变世界的动作先写 `*_intent` → 执行 → 再写 `*_done`；重启时 intent 无配对 done → 比对文件 hash → 补记或重做（第 4.3 节）。
5. **编排者只管分解/委派/验收**：任务卡 schema 强制四要素——目标、输出格式、工具白名单、边界；NPC 数量按复杂度缩放（铁律②）。
6. **制作者与检查者物理分离**：审查 NPC 的 context 在 `assembleContext(role)` 中**代码硬过滤** think 类事件，不允许 prompt 层绕过；审查者只看事实（代码/diff/测试结果），不看叙述（铁律③，ADR-002）。
7. **think 双读者规则**：同一份 `npc_think`/`guide_think` 对人类全透明（悬停/详情面板），对审查 NPC 永不可见——一份数据两条可见性规则，落实到 schema 与可见性矩阵（ADR-002，第 5 节）。
8. **每步可验证、三层强约束**：验证条件必须 `machine_verifiable`（command + expected exit_code），验证 NPC 只执行不判断；测试与实现分离（test-first，测试不得由写代码的 NPC 编写）；写不出可验证条件只有"继续拆解"或"转 HITL"两个出口（铁律⑧，ADR-004，第 6 节）。
9. **状态-动画严格映射**：每个动画状态严格对应一个 Harness 真实状态，闲置行为语义化（空闲=可分配）；协议表外不加无语义装饰动画，新增动画须先在第 7 节登记（ADR-003）。
10. **恢复状态不恢复思维**：状态（日志+worktree+任务板）持久化全量可得；思维（LLM 上下文、进行中推理）不快照不序列化，丢失即重建（第 8 节）。
11. **教原则不教步骤**：角色文件写原则（"密码永不明文"），不写步骤（"先 bcrypt 再 JWT"）（铁律⑥）。
12. **角色即插件**：NPC 角色 = `roles/` 下的 `.md`（YAML frontmatter + 系统 prompt）；同 role 类型可多实例并行；默认并发上限 `max_concurrent_agents=10` 可调（ADR-005）。

### 禁止做的（明确边界）

1. **禁止 View 直接写状态**：任何绕过 `user_*` 事件、让游戏层直接改 Session 或触发副作用的做法。
2. **禁止 UPDATE/DELETE Session 事件**：包括"就地压缩历史"或删除旧事件。
3. **禁止把 think 事件喂给审查 NPC**：包括以 prompt"提醒别看"代替代码硬过滤；隔离必须在 context 装配层物理生效。
4. **禁止自然语言验证条件**：不允许"看起来能用"式判断；禁止 Guide 在写不出可执行条件时编造假验证条件。
5. **禁止由写代码的 NPC 编写自己的验收测试**：消灭自审闭环（第 6 节第二层）。
6. **禁止 Sandbox 间共享可写文件系统**：跨 NPC 产物交换只走 Guide 仲裁的 git merge。
7. **禁止协议表外的装饰动画**：每个动画必须映射一个真实状态并在第 7 节登记。
8. **禁止引入跨任务 NPC 长期记忆（角色成长系统）**：v0.1 明确不做，防范围蔓延（第 13 节）。
9. **禁止使用泰拉瑞亚原素材 / 角色名原文 / 商标**：NPC 用职业泛称规避 IP；M3 前用占位色块。

## 当前状态

- **第 0 天**，仅有骨架目录和 ARCHITECTURE.md（设计基线），无任何代码。
- **第一个目标：M1.0 — 完成 `docs/contracts/` 下三个 schema 文件**
  - `events.schema.json` —— 14 种事件类型（user_command / user_interact / guide_think / guide_delegate / npc_think / tool_intent / tool_done / review_request / review_verdict / verify_run / merge / hitl_request / hitl_response / error）
  - `task_card.schema.json` —— 任务卡四要素（目标、输出格式、工具白名单、边界）+ 验证条件
  - `verification.schema.json` —— `machine_verifiable` 强约束（command + expected exit_code + type）
- 约束：本阶段仅产出文档与 schema，不写 Python/TypeScript/Rust 代码；README.md 保持空白，待 M1 出 demo 后再写。

## 后续里程碑

（按 ARCHITECTURE.md 第 12 节，先 Harness 后游戏）

- **M1（第 1-2 周）｜无头 Harness**：Session 日志 + WAL + Guide 分解委派 + 单 NPC 执行 + 验证三层。纯 CLI。验收：一条模糊指令 → 自动拆解 → 执行 → 验证 → 日志完整可回放。
- **M2（第 3 周）｜多 NPC + 审查闭环**：并行 worktree、裁缝审查（context 隔离）、退回重做循环。验收：强杀进程重启后无缝续作。
- **M3（第 4-5 周）｜像素小镇 View**：Phaser 渲染 + 状态-动画协议 + catch-up/live 订阅 + 悬停看 think。验收：一眼读出全系统状态。
- **M4（第 6 周）｜双向交互**：与向导对话下任务、点击 NPC 干预、HITL 敲玻璃。验收：全程不开终端完成一次真实开发任务。
- **M5（第 7-8 周）｜打磨与发布**：自定义角色、演示视频（含强杀重启镜头）、开源发布。
