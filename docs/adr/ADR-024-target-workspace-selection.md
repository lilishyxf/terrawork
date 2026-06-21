# ADR-024: 目标工作区选择(在真实项目上干活)

## 背景
ADR-023 让 advance 只 merge 进一次性沙箱(保护 TerraWorks 自身开发仓库)。但"在沙箱造贪吃蛇"
显不出多 Agent 价值,也不是真编程 agent。用户要像主流 coding agent 一样**指定自己的项目仓库**,
让一队 NPC 在其上并行、可验证地改代码并 merge。

## 决策
- 工作区(`repo_root`)**可运行时切换**:`create_app` 内用可变持有者 `_ws["root"]`(默认仍为沙箱),
  `_run_loop` 调 advance 时读当前值;文件浏览/预览端点亦读当前值。
- 新端点:
  - `GET /workspace` → `{path, is_git}`
  - `POST /workspace {path}` → 切到目标仓库。
- 切换时**规整 + 守卫**(不改编排核心的 `base_branch="main"`):
  - 拒绝指向 **TerraWorks 产品仓库自身**(`parents[2]`)——防污染本项目。
  - 非 git 目录 → `git init -b main` + 初始提交(本地身份)。
  - 已是 git → 要求**工作区干净**(否则 409);确保 `main` 分支存在(缺则从当前 HEAD 建,不动原分支)并 checkout。
  - 选"直接指向项目 + merge 主干":NPC 产物经验证+双审查后 merge 进该仓库 `main`(git 全程兜底,可 diff 可回滚)。
- 预览改为动态路由 `GET /workspace/raw/{path}`(替代固定 StaticFiles 挂载),随工作区变化;
  文件浏览 `GET /workspace/tree`、`GET /workspace/file`。
- `_git` 显式 `encoding="utf-8"`:中文 Windows 默认 GBK 解 git 输出会崩。

## 影响 / 约束
- 不动编排核心(create_worktree/merge 仍 `base_branch="main"`),靠"切换时规整到 main"避开 base_branch 全链穿参的回归面。
- 安全边界:拒产品仓库 + 要求干净 + git 兜底;NPC 改动皆为分支 + merge 提交,用户可审可回滚。
- 默认仍是沙箱(ADR-023 不变);切真实项目是用户显式动作。

## 备选(未采纳)
- 把仓库当前分支作为 base_branch 全链穿参(更通用,但要改 orchestrator/executor/子进程边界,回归面大)——
  改用"规整到 main"达到同等可用性,后续如需多分支再评估。
- 只进工作分支不 merge 主干(更保守)——用户选了"直接 merge 主干"。
