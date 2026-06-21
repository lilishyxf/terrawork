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


def _base_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "TerraWorks"
    base.mkdir(parents=True, exist_ok=True)
    return base


def main() -> None:
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
