# ADR-023: 沙箱仓库隔离 + 向导对话回路(M5 修复)

## 背景

M5 联调中,用户在指令栏发了一句"你好",系统却:
1. 把它分解成占位任务卡 `t-no-tasks`,派给 builder;
2. builder 凭空建了 `login.html`,违反任务卡边界;
3. 审查按边界打回,返工 2 轮仍不过 → 弹 HITL 惊动用户。

排查中又发现一个**更严重**的问题:advance 的 git merge 把 NPC builder 产物**真的合进了 TerraWorks 产品仓库的 main**——`serve.py --repo-root` 默认 `.`(产品仓库本身),于是 demo 产物(login.html、`frontend#1: t-create-login-page` 等提交、`npc/*` 分支)污染了产品历史。这与 M3-4 那次"流氓提交"同源:**编排引擎不该把 NPC 产物 merge 进它自己所在的产品仓库**。

两处根因:
- **非任务输入无出口**:`roles/guide.md` 写死"整个分解 1~6 张任务卡",`parser.py` 硬性拒绝空 `tasks`("Guide 必须产出至少 1 张任务卡")。两道都堵死,LLM 收到问候只能硬挤占位卡,然后照走 建→验→审 流水线,必然崩。
- **无沙箱隔离**:`--repo-root` 默认产品仓库,merge 落地即污染。

## 决策

### 1. 沙箱仓库隔离(防污染)
- `serve.py` 默认 `--repo-root` 改为 `data/sandbox-repo`(gitignored;首次启动 `git init -b main` + 本地身份 + 一条初始提交,使 NPC worktree 能从 main 切)。
- 启动时若 `--repo-root` 解析到**产品仓库根**(`harness/view/serve.py` 的 `parents[2]`),**直接拒绝运行**并报错,不进 uvicorn。
- 铁律:**NPC 产物只 merge 进一次性沙箱,永不进产品仓库**。沙箱可随时删除重建。

### 2. 向导对话回路(非任务输入短路)
- `roles/guide.md` 增"先判断:任务还是闲聊"一节:问候/寒暄/感谢/纯提问/无关闲聊 = 非任务 → 返回 `tasks: []` 空数组,`thinking` 写一句**直接对用户的友好回复**;**绝不为非任务输入编造占位卡**。
- `parser.py` 放开空 `tasks`:合法,仅产出 `[guide_think]`(thinking 即回复),无 `guide_delegate` → 编排器自然不派工(命令已有子事件 guide_think,不会被重复分解)。
- 前端 `App.tsx` 增 `computeGuideReply`:父为 `user_command`、且无 `guide_delegate` 子节点的最新 `guide_think` = 向导回复,在指令栏下方显示"向导:…"。

## 影响 / 约束

- **不新增事件类型**:对话回复复用既有 `guide_think`(ADR-002 对人可见、对审查 NPC 物理隔离),不动 15 类事件 schema。
- **不违反 §1 红线#1**:仍是 user 事件 → Harness 消费 → 投影 → View。
- **不违反铁律②**:编排器仍只分解/委派/验收;"零任务"是合法的分解结论(判定无需建卡),非绕过。
- 既有离线测试不依赖"空 tasks 报错";沙箱默认对 demo 透明(首次自动建)。

## 备选(未采纳)
- 新增 `guide_message` 事件类型做对话:否——动 schema 契约、增可见性矩阵负担,而 `guide_think` 已满足。
- 仅静默不建卡、不显示回复:否——用户发"你好"会感觉无响应,体验仍像坏的。
- 仅加守卫不改默认 repo-root:否——默认不安全等于把污染留给下一次手滑。
