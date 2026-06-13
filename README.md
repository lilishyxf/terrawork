<!-- README 正文待 M1 出 demo 后再写（见 PROJECT.md 当前状态）。此前仅维护「开发环境」一节。 -->

# TerraWorks

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
python -m pytest harness/tests -v
```

> 约定：文档/脚本中出现的 `python` 均指 `.venv` 内解释器（已 activate）。未激活时请用 `.venv\Scripts\python.exe` 显式指定，避免误用系统 3.13。`.venv/` 已在 `.gitignore` 中忽略，不入库。

### 复现验收

日常开发安装：`pip install -r harness/requirements.txt`（给宽松上界，允许小版本升级）
复现 M1.x 验收时安装：`pip install -r harness/requirements.lock`（精确版本，与最近一次 acceptance.md 记录的版本一致）
