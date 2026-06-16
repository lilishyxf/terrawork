# ARCHITECTURE.md — 多Agent像素小镇编排平台

> 项目名：**TerraWorks**（Terra 取自拉丁语「大地」，亦呼应启发本项目美学的 Terraria；Works = 作坊）。曾用工作名 Township（M0 草案阶段）。
> 版本：v0.1 架构基线 ｜ 状态：设计冻结前的最后评审稿
> 美术风格：类泰拉瑞亚 2D 像素风（**不得**直接使用泰拉瑞亚的美术资产、角色名称原文与商标，NPC 命名采用职业泛称，避免 IP 风险）

---

## 0. 定位宣言

**TerraWorks 是一个游戏化的多 Agent 编排平台，不是一个 Agent 监控皮肤。**

- 像素小镇是**操作界面**，不是装饰：玩家与向导对话 = 下达任务；点击 NPC = 查看与干预；一屏世界 = 系统全量健康状态。
- 游戏化是**信息密度手段**：一眼扫过小镇获取的状态信息（谁在干活、谁卡住、谁在等审查、任务流向哪、哪里需要人）必须超过一屏终端日志。达不到这个标准的游戏化元素一律砍掉。
- 与现有同类（Pixel Agents / AgentRoom / OpenClaw 像素办公室）的本质区别：它们是**被动可视化**（trace → 动画的单向镜子）；TerraWorks 是**双向编排**（动画 ⇄ Harness 的操作闭环），**且领域无关——软件工程是 flagship 示范场景，角色生态向其他领域开放**。

---

## 1. 顶层抽象：三层分离 + 独立 View

来自 Scaling Managed Agents 的三大抽象，外加本项目新增的 View 层：

```
┌─────────────────────────────────────────────────┐
│  View（像素小镇）   订阅 Session 事件，只读渲染      │
│                    玩家交互 → 转为 user 事件写回     │
└────────────────────────△────────────────────────┘
                         │ 事件订阅（WebSocket / SSE）
┌────────────────────────┴────────────────────────┐
│  Session（事件日志）  磁盘上、仅追加、永不删除        │
│                      SQLite WAL 模式              │
└────────────────────────△────────────────────────┘
                         │ 读 / 追加
┌────────────────────────┴────────────────────────┐
│  Harness（大脑/Guide） 无状态，崩溃可重启           │
│                       wake(sessionId) 续作        │
└────────────────────────△────────────────────────┘
                         │ 派发 / 回收
┌────────────────────────┴────────────────────────┐
│  Sandbox（手/NPC执行环境） 每 NPC 独立 worktree     │
│                          随时创建/销毁，互不干扰     │
└─────────────────────────────────────────────────┘
```

**职责红线（违反即架构腐化）：**

1. View **只能**从 Session 读状态，**禁止**直接写状态。玩家的一切交互必须转换为 `user_*` 类型事件追加进 Session，由 Harness 消费后产生后续事件。（CQRS 单向数据流）
2. Harness 不持有任何不可重建的内存状态。任何重要决策必须先落日志再生效。
3. Sandbox 之间不共享可写文件系统。跨 NPC 的产物交换只通过 git merge 走 Guide 仲裁。
4. Session 日志只追加。任何"压缩""清理""归档"操作都生成新文件，原始日志不动。

---

## 2. 八条铁律 → 实现承诺

| # | 铁律 | 来源 | TerraWorks 的实现 |
|---|------|------|----------------|
| ① | 三大抽象解耦 | Scaling Managed Agents | 第 1 节四层架构；Guide 崩溃 → `wake(sessionId)` 从日志续作 |
| ② | 编排者只管分解/委派/验收 | Multi-Agent Research System | 任务卡 schema 强制四要素：目标、输出格式、工具白名单、边界；按复杂度缩放 NPC 数量 |
| ③ | 制作者与检查者分离 | Loop Engineering / Auto Mode | 审查 NPC 的 context 装配时**物理剥离**制作 NPC 的 think 事件（见第 5 节可见性矩阵） |
| ④ | 上下文在窗口之外 | Scaling Managed Agents | Session 日志为唯一事实源；上下文按需分层注入（见第 8 节） |
| ⑤ | 复杂任务强制 think 暂停 | The Think Tool | Guide 评估任务复杂度，仅复杂任务在 NPC 指令中注入 think 协议；简单任务不用 |
| ⑥ | 教原则不教步骤 | Teaching Claude Why | NPC 角色文件写原则（"密码永不明文"），不写步骤（"先 bcrypt 再 JWT"） |
| ⑦ | 循环五部件 | Loop Engineering | 自动化触发器 + 独立 worktree + 磁盘记忆 + MCP 连接器 + 制作/检查子代理分离 |
| ⑧ | 每步可验证 | Long-running Agents | 验证条件三层方案（见第 6 节）；不可机器验证 → 继续拆解或转 HITL |

---

## 3. 核心设计决策记录（ADR）

### ADR-001：游戏世界 = 独立 View 层（订阅者），非 Harness 显示层

**决策**：选项 B。游戏 Client 是 Session 日志的 subscriber，自己维护动画状态，对事件做插值动画。

**理由**：动画连续性是游戏体验的底线；Harness 与 View 的崩溃域分离后各自可独立重启。

**实现要点 — 追赶模式（Catch-up Protocol）**：

```
游戏启动：
  Phase 1 (Catch-up): 按 last_event_id 游标批量拉取历史事件
                      → 快进重建状态（不播动画，直接定位）
  Phase 2 (Live):     切换 WebSocket 实时订阅
                      → 事件逐条到达，播放完整动画
```

### ADR-002：think 透明度 — 对人全透明，对审查 Agent 物理隔离

**决策**：选项 3 升级版。think 期间 NPC 播放"思考动画"，鼠标悬停展示 think 全文。

**关键澄清**：think 内容有两类读者，待遇完全不同——

| 读者 | 可见性 | 通道 |
|---|---|---|
| 人类用户 | 全透明（悬停/详情面板） | UI 直接读 Session 日志 |
| 审查 NPC（如裁缝） | **永不可见** | Harness 装配 context 时硬过滤 think 类事件 |

一份数据，两条可见性规则。既不黑盒（用户可全程判断），又保住检查者独立性（铁律③）。该规则在 `assembleContext(agent_role)` 中以代码硬编码，不允许 prompt 层面绕过。

### ADR-003：状态-动画严格映射，闲置行为语义化

**决策**：每个动画状态严格映射一个 Harness 真实状态。不做随机装饰动画。无任务 NPC 回小屋睡觉/钓鱼——闲置本身就是信息（资源空闲可分配）。

完整映射协议见第 7 节。

### ADR-004：验证条件由 Guide 生成，以三层形式约束保真

**决策**：不引入"审查验证条件的 agent"（递归无底洞），改用形式约束 + 测试实现分离 + HITL 兜底。详见第 6 节。

### ADR-005：并发上限与角色可插拔

- 同时活跃 NPC 上限默认 10，配置项 `max_concurrent_agents` 可调（成本与 sandbox 资源约束）。
- NPC 角色 = `roles/` 目录下的 `.md` 文件（YAML frontmatter + 系统 prompt），用户自定义提示词即放文件，角色即插件。

---

## 4. Session 日志规范

### 4.1 存储

- SQLite（WAL 模式），单文件 `data/session.db`。
- 事件表仅 INSERT，禁止 UPDATE/DELETE。
- 每事件字段：`event_id`（自增）、`session_id`、`ts`、`agent`、`type`、`payload`（JSON）、`parent_event_id`（因果链）。

### 4.2 事件类型（v0.1 最小集）

```
user_command        玩家输入的任务/指令
user_interact       玩家点击/对话某 NPC
guide_think         Guide 的推理（对人可见，对 NPC 不注入）
guide_delegate      派发任务卡（含四要素 + 验证条件）
npc_think           NPC 推理（对人可见，对审查 NPC 隔离）
tool_intent         工具调用意图（WAL 前置写入）
tool_done           工具调用完成（含结果 hash）
review_request      送审
review_verdict      审查结论（pass / reject + notes）
verify_run          验证条件执行记录（command + exit_code + output 摘要）
merge               Guide 仲裁合并
hitl_request        请求人类介入（NPC 敲玻璃）
hitl_response       人类的回应
error               异常/卡死/循环检测
```

### 4.3 预写规则（Write-Ahead，崩溃一致性的根基）

**所有改变世界的动作：先写意图事件 → 执行 → 再写完成事件。**

```
{type: "tool_intent", tool: "write_file", file: "auth.ts", agent: "merchant"}   ← 先落盘
[实际写文件]
{type: "tool_done",   file: "auth.ts", hash: "a3f2..."}                          ← 再落盘
```

重启恢复时发现 intent 无配对 done → 状态不明 → Guide 比对文件实际 hash → 决定重做或补记。

---

## 5. 可见性矩阵（Context 装配规则）

`assembleContext(role, task)` 按下表硬过滤事件类型：

| 事件类型 | 人类 UI | Guide | 制作 NPC（本人） | 制作 NPC（他人） | 审查 NPC |
|---|---|---|---|---|---|
| user_command | ✅ | ✅ | 任务相关 | ❌ | 任务相关 |
| guide_think | ✅ | ✅ | ❌ | ❌ | ❌ |
| npc_think | ✅ | 摘要 | ✅（自己的） | ❌ | **❌ 物理隔离** |
| tool_intent/done | ✅ | ✅ | ✅（自己的） | ❌ | ✅（代码与文件事实） |
| review_verdict | ✅ | ✅ | ✅（针对自己的） | ❌ | ✅（自己出具的） |
| verify_run | ✅ | ✅ | ✅ | ❌ | ✅ |

原则：**审查者只看事实（代码、diff、测试结果），不看叙述（推理、辩解）**——防止制作 NPC "说服"审查 NPC。

---

## 6. 验证条件三层方案（ADR-004 详述）

**问题**：铁律⑧假设验证条件由人写；本系统中用户只给模糊任务（"做个登录"），验证条件由 Guide（LLM）生成。坏的验证条件（太松/跑偏/自我实现）会让整个验证体系空转，且"用 LLM 审查 LLM 生成的验证条件"递归无底。

**解法：不靠更多审查，靠形式约束。**

**第一层 — 格式上强制可执行。** 验证条件不允许是自然语言判断，schema 强约束为结构化断言（`expected` 为对象而非字符串谓词，详见 ADR-006）：

```json
{
  "type": "machine_verifiable",
  "command": "npm test -- auth",
  "expected": { "exit_code": 0 }
}
```

`expected` 强制 `exit_code`，可叠加 `stdout_contains` / `stdout_matches` / `stderr_empty` / `artifact` 等结构化断言；`additionalProperties: false` 杜绝自然语言谓词回流。验证 NPC（爆破专家）只做字段比对、不解析谓词、不判断。信任问题 → 执行问题。

**第二层 — 测试与实现分离。** 验证用测试不得由写代码的 NPC 编写：优先用仓库既有测试；新功能由 Guide 先派**另一个 NPC** 写测试（test-first），制作 NPC 的任务定义为"让这些已存在的测试通过"。

**第三层 — 不可验证则上抬。** Guide 拆解后写不出 machine_verifiable 条件的子任务，禁止编造假条件，只有两个出口：继续拆解直到可验证，或标记 `hitl_request`（NPC 走到屏幕前敲玻璃，人工验收）。

递归终止证明：第一层保证可执行，第二层消灭自审，第三层兜住漏网 → 无需"验证验证条件的 agent"。

---

## 7. 状态-动画协议（View 层核心资产）

| Harness 真实状态 | 游戏表现 | 信息含义 |
|---|---|---|
| 无任务 | 回小屋睡觉 / 河边钓鱼 | 资源空闲，可分配 |
| 任务排队中 | 站在任务板前看板子 | 已分配未开始 |
| LLM 推理中（think） | 工位托腮 + 思考泡（悬停见全文） | 正在思考，勿扰 |
| 工具执行中 | 打字 / 翻书 / 敲打 | 正在干活 |
| 等待审查 | 在审查者门口踱步 | 流程阻塞在审查 |
| 被退回返工 | 挠头走回工位 | 上轮未过 |
| 验证执行中 | 爆破专家点引线 | 测试在跑 |
| 卡死 / 循环 / 报错 | 工位冒烟 + 红色感叹号 | 需要关注 |
| 等待人类（HITL） | **走到屏幕前敲玻璃** | 需要你，最高优先级 |
| 合并完成 | 小镇钟楼敲钟 | 里程碑达成 |

约束：协议表之外不添加无语义的装饰动画。每新增一个动画必须先在此表登记其映射的真实状态。

---

## 8. 崩溃恢复与重启机制

**原则：恢复状态，不恢复思维。**

- **状态**（Session 日志 + worktree 文件 + 任务板）：磁盘持久化，重启全量可得。
- **思维**（LLM 上下文窗口、进行中的推理）：不持久化、不快照、不序列化。丢失成本 = 几秒推理 + 几分钱 token，重建成本远低于恢复机制的复杂度。

**重启序列**：

```
1. Harness 启动 → wake(sessionId)
2. 扫描日志：找出所有 intent 无配对 done 的事件 → 比对实际文件 hash → 补记或重做
3. 重建任务板视图：哪些任务 done / in-progress / queued
4. 对 in-progress 任务：重新派发（任务卡自包含，新 LLM 调用从
   任务描述 + 该任务相关历史事件重建工作 context）
5. View 层 Catch-up → Live
```

**上下文重建预算（分层注入）**：

| 层 | 内容 | 注入方式 |
|---|---|---|
| L1 | 当前任务的完整事件链 | 全量注入 |
| L2 | 本任务涉及文件的历史操作 | 摘要注入 |
| L3 | 其余全部历史 | 不注入；提供 `query_session(filter)` 工具让 NPC 按需自查 |

不做不可逆压缩（铁律④）；把"查历史的能力"交给 agent，而非把历史塞进窗口。

**演示场景（验收标准之一）**：录屏中强杀应用 → 重启 → 小人各就各位、任务无缝续作。该场景必须始终可复现。

---

## 9. 角色系统

### 9.1 角色文件格式

```markdown
---
name: merchant            # 内部 ID
display_name: 商人         # 游戏内名称（职业泛称，规避 IP）
role: builder             # builder / reviewer / verifier / orchestrator
model: deepseek-chat      # 可按角色配不同模型
tools: [read, write, bash]
max_think_depth: 3
sprite: merchant.png      # 像素形象
idle_behavior: shop       # 闲置行为：看店
---

## 原则（不写步骤）
- 密码永不明文存储
- 一切用户输入必须校验
- 错误信息不泄露内部状态
...
```

### 9.2 v0.1 初始角色表

| 角色 | 职责 | 工程映射 |
|---|---|---|
| 向导（Guide） | 分解 / 委派 / 验收 / 仲裁合并 | Orchestrator（Harness 本体） |
| 商人 | 功能实现 | Builder |
| 护士 | Bug 修复 | Builder（修复向） |
| 哥布林修补匠 | 重构 / 架构改造 | Builder（重构向） |
| 裁缝 | 代码审查 | Reviewer（context 物理隔离 think） |
| 爆破专家 | 跑测试 / 执行验证条件 | Verifier（只执行不判断） |

> 用户可向 `roles/` 添加任意角色文件扩展团队；同 role 类型可多实例并行。

---

## 10. 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 桌面壳 | Tauri 2.0 | 体积/内存优势；alwaysOnTop 等窗口控制；Rust 后端对事件流友好 |
| 游戏渲染 | Phaser 3（WebView 内） | 2D 像素游戏成熟引擎；Pixel Agents 同技术路线可参考其开源实现 |
| 前端框架 | React 18 + TypeScript | 非游戏 UI（详情面板、设置、任务板）|
| Harness | Python 3.11 + FastAPI（sidecar） | LLM 编排生态最全；与既有经验复用 |
| Session 存储 | SQLite（WAL 模式） | 单文件、内建预写日志、零运维 |
| LLM | 可插拔（DeepSeek / Claude / 任意 OpenAI 兼容） | 角色级配置，不绑死供应商 |
| Agent 执行 | 子进程 + git worktree 隔离 | 铁律⑦；轻量于容器，够用 |
| 事件推送 | WebSocket（Harness → View） | Catch-up + Live 双阶段协议 |

---

## 11. 目录结构

```
township/
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
│   ├── ARCHITECTURE.md       # 本文件
│   ├── adr/                  # 决策记录，新决策追加编号
│   └── contracts/            # 事件 schema、任务卡 schema、验证条件 schema
└── data/                     # session.db、worktrees（gitignored）
```

---

## 12. 构建顺序（里程碑）

> 原则沿用：每周交付一个能 demo 的能力，不堆叠半成品。**先 Harness 后游戏**——编排引擎是别人没有的部分，像素层是可参考开源的部分。

- **M1（第 1-2 周）｜无头 Harness**：Session 日志 + WAL + Guide 分解委派 + 单 NPC 执行 + 验证条件三层。纯 CLI，无游戏。验收：一条模糊指令 → 自动拆解 → 执行 → 验证 → 日志完整可回放。
- **M2（第 3 周）｜多 NPC + 审查闭环**：并行 worktree、裁缝审查（context 隔离）、退回重做循环。验收：强杀进程重启后无缝续作。
- **M3（第 4-5 周）｜像素小镇 View**：Phaser 渲染 + 状态-动画协议 + catch-up/live 订阅 + 悬停看 think。验收：一眼读出全系统状态。
- **M4（第 6 周）｜双向交互**：与向导对话下任务、点击 NPC 干预、HITL 敲玻璃。验收：全程不开终端完成一次真实开发任务。
- **M5（第 7-8 周）｜打磨与发布**：自定义角色、演示视频（含强杀重启镜头）、开源发布。

---

## 13. 已知风险与未决问题

1. **窗口期风险**：Pixel Agents 已声明 agent-agnostic 编排愿景；本项目的速度策略是 M1-M2 优先（编排深度是其当前没有的），像素层站其开源肩膀。
2. **交互效率悖论**：游戏化交互不得比终端慢。设计基线：高频操作（下任务、看状态）必须 ≤ 终端等效操作的步数；低频操作才允许游戏化仪式感。每个交互设计对照此基线评审。
3. **美术资产**：类泰拉风格自绘或购买资产包，禁用泰拉瑞亚原素材。M3 前用占位色块开发，美术不阻塞工程。
4. **token 成本**：10 NPC 并行的成本上限需在 M2 实测后写入默认配置；Guide 用强模型、执行 NPC 用便宜模型的分级策略待验证。
5. **未决**：跨任务的 NPC 长期记忆（角色成长系统）是否引入 —— v0.1 明确不做，记录于此防止范围蔓延。

---

*本文件为架构基线。任何与本文件冲突的实现，以本文件为准；任何需要偏离本文件的决定，先提 ADR 追加到 docs/adr/ 再动代码。*
