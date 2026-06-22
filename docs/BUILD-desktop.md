# 打包桌面应用(Tauri + Python sidecar)

TerraWorks 桌面版 = Tauri 壳(前端 WebView)+ Python 后端打成 sidecar 自启。
sidecar 二进制不入库(`src-tauri/binaries/` 已 gitignore),按下面重建。

## 前置(一次性)
- Node + npm、Rust(rustup,MSVC 工具链)、WebView2(Win11 自带)
- Python venv 已装依赖 + PyInstaller:`.venv/Scripts/python.exe -m pip install pyinstaller`
- 目标机需有 **git**(NPC worktree 依赖)

## 1. 打包后端 sidecar(PyInstaller → 单文件 exe)
先预置 tiktoken 词表(否则打包版 litellm 会报 `Unknown encoding cl100k_base`):
```bash
mkdir -p build/tiktoken_cache
TIKTOKEN_CACHE_DIR="$(pwd)/build/tiktoken_cache" .venv/Scripts/python.exe -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
```
再打包(注意 tiktoken 的插件 + 词表必须一起打进去):
```bash
.venv/Scripts/python.exe -m PyInstaller --noconfirm --onefile --name terraworks-backend \
  --distpath build/pyi-dist --workpath build/pyi-work --specpath build \
  --add-data "<绝对路径>/roles;roles" \
  --add-data "<绝对路径>/docs/contracts;docs/contracts" \
  --add-data "<绝对路径>/build/tiktoken_cache;tiktoken_cache" \
  --collect-all litellm --collect-all uvicorn --collect-all tiktoken --collect-submodules harness \
  --hidden-import tiktoken_ext.openai_public --hidden-import tiktoken_ext \
  --hidden-import fastapi --hidden-import anyio \
  scripts/backend_entry.py
```
产出 `build/pyi-dist/terraworks-backend.exe`,复制为 Tauri sidecar 命名(带目标三元组):
```bash
triple=$(rustc -vV | sed -n 's/host: //p')   # 如 x86_64-pc-windows-msvc
cp build/pyi-dist/terraworks-backend.exe "src-tauri/binaries/terraworks-backend-$triple.exe"
```

## 2. 打包桌面 app(Tauri)
```bash
npx tauri build
```
产出(`src-tauri/target/release/bundle/`):
- `nsis/TerraWorks_<ver>_x64-setup.exe`(安装器,推荐)
- `msi/TerraWorks_<ver>_x64_en-US.msi`

## 运行期说明
- 壳启动时自动拉起 `terraworks-backend` sidecar(`src-tauri/src/lib.rs` setup 内 spawn,
  退出时 ExitRequested 杀进程);前端连 `127.0.0.1:8000`。
- 后端数据写到 **`%LOCALAPPDATA%/TerraWorks/`**(session.db、sandbox-repo、worktrees)。
- **API 密钥**:把 `.env`(含 DEEPSEEK_API_KEY 等)放到 `%LOCALAPPDATA%/TerraWorks/.env`;
  缺则只能跑 mock。后续可做设置界面内填。
- `backend_entry.py` 默认 `TERRA_LLM_MODE=real`。

## 待办(分发完善)
- app 图标换成 TerraWorks 像素 logo(`npx tauri icon <方形png>`)。
- 应用内填 API key 的设置界面(免手放 .env)。
- 首启检测 git 是否存在,缺失时友好提示。
