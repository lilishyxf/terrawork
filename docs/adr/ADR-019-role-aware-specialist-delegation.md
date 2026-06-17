# ADR-019: 角色感知的专家委派（向导自动组队）

## 背景

§0 定位宣言："领域无关——软件工程是 flagship，角色生态向其他领域开放"；铁律②（向导只管分解/委派/验收）；ADR-005（角色即插件）。产品核心循环是：**用户给需求 → 向导自动拆解并从专家库匹配出需要的 NPC → 各 NPC（一个 = 一个 agent）协作完成**。

recon 现状（缺口）：
- `task_card.assignee_role` 是**功能枚举** `builder/verifier/reviewer/orchestrator`，无"专长"维度。
- `roles/` 每个功能只有一个角色：builder=merchant、verifier=blaster、reviewer=tailor、orchestrator=guide。
- 向导分解（`guide.md` prompt + `build_messages`）**不知道有哪些专家**，产出的卡全是 `assignee_role=builder`。
- 编排器 `_builder_instance` / `ROLE_INSTANCE` **写死 merchant#N**。
- 所以无论什么活，干活的永远是"商人"——专家自动匹配**未实现**。

但**角色文件 frontmatter 已是双轴**（`role` 功能 + `specialty` 专长 + `domain`），且执行层 `execute_npc` 已按实例名 `<name>#N` 载入 `roles/<name>.md`——**底层已就绪，缺的是接线**。

## 决策

### 1. 正式确立双轴角色模型
- **功能轴 `role`**（builder/verifier/reviewer/orchestrator）：驱动编排闭环与 maker≠checker（铁律③）。所有专家 builder 都走同一条 验证→审查→merge 闭环、可并行（M2.5）。
- **专长轴**：由角色文件的 `name`（唯一标识，须满足实例 slug 正则 `^[a-z_][a-z0-9_]*$`，如 `frontend`/`backend`/`database`）确定提示词/专业/sprite。
- 一个 `roles/<name>.md` = 一个 (role, specialty) 组合 = 一个可被向导调度的 agent。

### 2. task_card 增 `assignee_specialty`（可选）
- 新增可选字段 `assignee_specialty`：值 = 某个 `role:builder` 角色文件的 `name`（如 `"frontend"`）。
- `assignee_role` 保留（功能轴，驱动闭环逻辑不变）；`assignee_specialty` 决定**由哪个专家**当这个 builder。
- **缺省（无 `assignee_specialty`）→ `merchant`**（向后兼容：现有 fixtures/测试不受影响）。
- schema 改动：`task_card.schema.json` 加 `assignee_specialty`（string，pattern `^[a-z_][a-z0-9_]*$`，optional）。这是契约改动，由本 ADR 承载。

### 3. 向导自动匹配（核心）
- `build_messages` **动态注入 builder 专家目录**：扫 `roles/` 中 `role:builder` 的角色，把 `name + display_name + specialty + 使用时机`（取自角色文件正文/frontmatter）列进向导 prompt。
- `guide.md` prompt 增"专家匹配"段：分解时按每个子任务的性质，把最合适专家的 `name` 填进卡的 `assignee_specialty`；说不清就留空（→ merchant 兜底）。
- 匹配是**向导的 LLM 判断**，非硬规则——符合"向导只管分解/委派"。

### 4. 编排器泛化（去掉 merchant 写死）
- 实例命名：builder 卡 → `<assignee_specialty>#<该专长的序号>`（如 `frontend#1`、`backend#1`；同专长多卡 → `frontend#1`/`frontend#2`）。无专长 → `merchant#N`。
- `_builder_instance` / `_builder_delegates` / `builder_insts` 按卡的 `assignee_specialty` 泛化；`_role_from_instance` 已能从实例名解析角色（零改）；`execute_npc` 已按角色载入 frontmatter（零改）。
- **test-author≠implementer（ADR-004）仍成立**：不同卡 → 不同实例（哪怕同专长，也是 `frontend#1` vs `frontend#2`）。

### 5. 双审查聚合（代码审查 + 安全审查）
- 阵容含两位 reviewer：`tailor`（代码审查）+ `appsec`（应用安全工程师，安全审查）。两者都在 checker 轴、看隔离事实 context（§5 / 铁律③）。
- 闭环改动：每张 builder 卡完工后**派发全部 reviewer**，`arbitrate` 改为 **全部 reviewer pass 才 merge；任一 reject → 返工**。
- verifier（blaster，机器验证 `verify_run`）仍是审查前的确定性闸，不变。
- **可配置/向后兼容**：reviewer 集合由"存在哪些 reviewer 角色"决定;仅 tailor 时退化为现有单审查闭环（现有测试不破）。appsec 加入后自动变双审查。

### 6. blaster 不算"prompt-agent"
- blaster `model:null`、确定性、无提示词 → 是机械验证位，不计入"10 个 agent"，但仍在闭环与小镇中。

## 阵容（锁定，M2.7-2 落角色库）
指挥官 1：`guide`（已存在，增专家匹配段）。
builder 专家 7：`frontend` 前端 / `backend` 后端 / `database` 数据库 / `desktop_shell` 桌面壳（Tauri/Electron/原生窗口，对齐 TerraWorks 桌面应用 flagship）/ `ai_engineer` AI / `rapid_proto` 快速原型 / `tech_writer` 技术写作。
reviewer 2：`tailor` 代码审查（已存在）/ `appsec` 应用安全（新增）。
机械验证位：`blaster`（已存在，不计入 10）。

## 不变量（INV）

data-scope（M2.7-1 fixture + 自洽 test）：
- **INV-1**：带 `assignee_specialty` 的任务卡过 `task_card.schema`；无该字段的卡仍合法（向后兼容）。
- **INV-2**：参考分解里，向导按子任务把卡匹配到不同专长（如"带登录的桌面记账应用" → UI 卡 `assignee_specialty=frontend`、逻辑卡 `=backend`、存储卡 `=database`、打包/窗口卡 `=desktop_shell`）。
- **INV-3**：每个 `assignee_specialty` 值都对应一个存在的 `role:builder` 角色文件 name。

runtime-scope（M2.7-3/4 e2e）：
- **INV-4**：编排器按卡专长实例化 `<specialty>#N`、载入对应角色文件，非写死 merchant。
- **INV-5**：test-author≠implementer 在专长模型下仍成立。
- **INV-6**：双审查——两位 reviewer 都 pass 才 merge；任一 reject 触发返工。
- **INV-7**：无 `assignee_specialty` 的旧式分解仍走 merchant 单审查闭环（零回归）。

## 后果

- **实现产品核心循环**：向导自动组队专家协作，§0/铁律②/ADR-005 真正落地；M3 小镇将渲染"被向导匹配出的、各司其职的真专家"，而非 10 个商人分身。
- **角色生态可长**：新专长 = 加一个 `roles/<name>.md`，向导目录自动收录、编排器自动实例化、View 按 name→sprite-key 渲染——零引擎改动（呼应 §0 开放）。
- **验证约束仍在**：无 machine_verifiable 产物的角色（如纯设计）只能 HITL；本阶段专家均为产出可验证代码的 builder。
- **契约改动**：仅 `task_card.schema.json` 加 `assignee_specialty`（可选、向后兼容）；events/verification schema 不变。
- **拆分实施**：M2.7-1 契约（本 ADR + `task_card.schema` 加 `assignee_specialty` + fixture + 自洽 test）/ M2.7-2 角色库 / M2.7-3 向导匹配（catalog 注入）+ 编排器泛化（按专长实例化）/ M2.7-4 双审查聚合。每步独立 gated、可单独回归。
- **不影响 M3**：本里程碑是后端编排能力;M3 View 解耦,且因有真专家而更有意义(建议 M2.7 先于 M3)。
