# TerraWorks

**游戏化的多 Agent 编排平台**：像素小镇是操作界面而非装饰——跟向导对话即下任务、点 NPC 即干预、一屏世界即系统全量健康状态。与同类（trace→动画的单向可视化）的本质区别是**双向编排**（动画 ⇄ Harness 操作闭环）。

> 设计基线见 [ARCHITECTURE.md](ARCHITECTURE.md)（冻结，偏离须先提 ADR）；工程速查与红线见 [PROJECT.md](PROJECT.md)；决策记录见 [docs/adr/](docs/adr/)，契约 schema 见 [docs/contracts/](docs/contracts/)。

## 当前状态（M1 + M2 全部达成，无头 Harness 可 demo）

已落地的是**纯后端 Harness**——三抽象中的 Session（仅追加事件日志）与 Sandbox（worktree 隔离）。像素小镇 View（M3）尚未开始，目前无 GUI；演示通过测试套件与日志回放 CLI 进行。

当前可端到端自动跑通的闭环：

```
用户一句模糊指令
  → Guide 分解为任务卡（test-first：测试卡 + 实现卡 depends_on 测试卡）
  → 多实例执行（测试作者 merchant#1 ≠ 实现者 merchant#2，物理隔离 context）
  → 验证（machine_verifiable：command + expected exit_code，验证者只执行不判断）
  → 裁缝审查（context 硬过滤 think 事件，只看事实不看叙述）
  → 退回重做循环（reject → 注入整改要点返工，≤ max_rework 次，超限转 HITL）
  → Guide 仲裁 → git merge 回主干
  → 全程仅追加日志；强杀进程后 advance() 从日志无缝续作（恢复状态不恢复思维）
```

里程碑进度（路线图见 ARCHITECTURE.md 第 12 节）：

| 里程碑 | 范围 | 状态 |
|---|---|---|
| M1 | Session 日志 + WAL + Guide 分解委派 + 单 NPC 执行 + 验证三层 | ✅ 达成（含真实 DeepSeek live 验收） |
| M2 核心 | 多 NPC + 裁缝审查（context 隔离）+ 退回重做 + 崩溃续作 + 多卡 test-first | ✅ 达成（§12 验收：强杀重启续作） |
| M2.6 真隔离 | per-card worktree + 真 git merge（红线 #6）+ merge-then-verify + NPC 子进程隔离 | ✅ 达成（ADR-016/017，离线 67 passed） |
| M2.5 真并行 | 多实例并发执行（独立卡）+ `max_concurrent_agents` | ✅ 达成（ADR-018，计时实证并发，70 passed） |
| M3+ | 像素小镇 View / 双向交互 / 打磨发布 | ⬜ 未开始 |

关键决策与偏差均记录在 [docs/adr/](docs/adr/)。M2.6 收尾了 §1 红线 #6（Sandbox 不共享可写 FS、跨 NPC 交换只走 git merge）：ADR-016（真 git merge + per-card worktree + merge-then-verify）superseded 了 ADR-014/015 的文件系统让步，ADR-017（NPC 子进程隔离）收尾了 ADR-012 的进程隔离。

## 开发环境

架构基线（ARCHITECTURE.md 第 10 节）规定 Harness 运行在 **Python 3.11.x**。所有命令一律走仓库内 `.venv` 的解释器；系统 `py` / `python`（可能是 3.13 等其他版本）仅用于创建 venv 等辅助操作，**不**用于跑 Harness 或验收。

### 一次性初始化

```bash
cd D:\Projects\TerraWorks
py -3.11 -m venv .venv
.venv\Scripts\activate
python --version                                  # 应为 3.11.x
python -m pip install -r harness/requirements.txt
python -m pip install -r harness/requirements-dev.txt
```

### 日常使用

```bash
.venv\Scripts\activate                            # 每个新终端先激活
python -c "import jsonschema, referencing; print('ok')"
```

> 约定：文档/脚本中出现的 `python` 均指 `.venv` 内解释器（已 activate）。未激活时请用 `.venv\Scripts\python.exe` 显式指定，避免误用系统 3.13。`.venv/` 已在 `.gitignore` 中忽略，不入库。

### 复现验收

- 日常开发安装：`pip install -r harness/requirements.txt`（宽松上界，允许小版本升级）
- 复现 M1.x 验收时安装：`pip install -r harness/requirements.lock`（精确版本，与最近一次 acceptance.md 记录一致）

## 跑测试 / 看 demo

测试套件即全闭环的可执行演示，端到端覆盖上述编排环路（happy path / 崩溃续作 / 退回重做 / 多卡 test-first）。

```bash
# 离线全回归（纯数据/mock，无 API 调用，秒级）
.venv\Scripts\python.exe -m pytest harness/tests -m "not live" -q

# 含 live 验收（需 .env 内 DeepSeek key，真调一次 LLM，~20s/少量 token）
.venv\Scripts\python.exe -m pytest harness/tests
```

> `live` marker 标注需真实 LLM key 的用例（见 [pytest.ini](pytest.ini)）；离线运行用 `-m "not live"` 排除。API key 仅放入 `.env`（已 gitignored），不在命令行出现。

### 回放 Session 日志

Session 是唯一事实源；以下 CLI 可建库、灌事件、按因果链回放，验证"日志完整可回放"：

```bash
python -m harness.session.cli --db data/session.db init
python -m harness.session.cli --db data/session.db dump          # 按 parent_event_id 打印因果树
python -m harness.session.cli --db data/session.db query --type review_verdict
```

## 目录

```
harness/        Python sidecar：guide/(编排) session/(日志+WAL) context/(可见性矩阵)
                sandbox/(worktree + 执行 + 验证 + 审查) tests/
roles/          角色定义（用户可扩展的 .md：guide/merchant/blaster/tailor）
docs/contracts/ events / task_card / verification 三个 JSON schema（契约）
docs/adr/       架构决策记录
data/           session.db、worktrees（gitignored）
```
