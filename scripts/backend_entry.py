"""TerraWorks 后端打包入口(PyInstaller → Tauri sidecar)。

打成单文件 exe,由 Tauri 壳启动时自动拉起。与开发期 `python -m harness.view.serve` 等价,
区别:
- 数据写到**可写的用户目录** %LOCALAPPDATA%/TerraWorks(安装目录 Program Files 不可写);
- .env 从该用户目录读(密钥不打进 exe);
- roles/、docs/contracts/ 由 PyInstaller --add-data 打进包,harness 按 __file__ 解析即命中。
"""
import os
import sys
from pathlib import Path

# 打包(frozen)时把 tiktoken 词表指向内置缓存,避免运行时联网下载 + 缺插件报
# "Unknown encoding cl100k_base"(litellm 加载即崩)。须在 import litellm 前设好。
if getattr(sys, "frozen", False):
    _cache = Path(sys._MEIPASS) / "tiktoken_cache"  # type: ignore[attr-defined]
    if _cache.is_dir():
        os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(_cache))


def _base_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "TerraWorks"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _pick_dir_mode() -> bool:
    """打包版子命令:`terraworks-backend.exe --pick-dir` 只弹系统文件夹对话框、打印路径、退出。
    (frozen 下不能用 sys.executable -c 跑 python,故用本子命令代替。)"""
    if "--pick-dir" not in sys.argv:
        return False
    try:
        import tkinter as tk
        from tkinter import filedialog
        r = tk.Tk(); r.withdraw(); r.attributes("-topmost", True)
        p = filedialog.askdirectory(title="选择 NPC 要干活的项目文件夹")
        r.destroy()
        print(p or "")
    except Exception:
        print("")
    return True


def main() -> None:
    if _pick_dir_mode():
        return
    base = _base_dir()

    # 密钥:从用户数据目录读 .env(用户放这里);默认实时模式
    try:
        from dotenv import load_dotenv
        load_dotenv(base / ".env")
    except ImportError:
        pass
    os.environ.setdefault("TERRA_LLM_MODE", "real")

    import uvicorn
    from harness.view.server import create_app
    from harness.view.serve import ensure_sandbox_repo

    repo_root = ensure_sandbox_repo(base / "sandbox-repo")
    app = create_app(
        str(base / "session.db"),
        repo_root=repo_root,
        worktrees_base=(base / "worktrees").resolve(),
    )
    print(f"[terraworks-backend] 数据目录:{base}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
