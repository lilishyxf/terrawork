"""手动启动 View 事件服务(M3-2/3)。

    python -m harness.view.serve --db data/session.db --port 8000

配合前端:另开终端 `npm run dev`(Vite :5173),页面连本服务的 :8000。

ADR-023 沙箱隔离:advance 会把 NPC builder 产物真 merge 进 --repo-root 的 main。
该仓库**必须是一次性沙箱**,绝不能是 TerraWorks 产品仓库本身——否则 demo 产物
(login.html 之类)会被提交进产品仓库历史。故:
  - 默认 repo-root = data/sandbox-repo(首次自动 git init,gitignored);
  - 启动时若 repo-root 解析到产品仓库根,直接拒绝运行。
"""
import argparse
import subprocess
from pathlib import Path

import uvicorn

from harness.view.server import create_app

# 产品仓库根 = 本文件的 harness/view/serve.py 往上三级
PRODUCT_ROOT = Path(__file__).resolve().parents[2]

# 加载 .env,使实时模式(TERRA_LLM_MODE=real)能从环境读到 *_API_KEY。
# 之前只有测试 conftest 加载 .env,生产启动器漏了 → 实时调用拿不到 key。
try:
    from dotenv import load_dotenv
    load_dotenv(PRODUCT_ROOT / ".env")
except ImportError:
    pass


def ensure_sandbox_repo(repo_root: Path) -> Path:
    """确保 repo_root 是一个可用的沙箱 git 仓库;拒绝指向产品仓库本身。

    返回解析后的绝对路径。首次调用会 git init + 一条初始提交(让 worktree 能从 main 切)。
    """
    resolved = repo_root.resolve()
    if resolved == PRODUCT_ROOT:
        raise SystemExit(
            f"拒绝运行:--repo-root 指向了 TerraWorks 产品仓库本身({resolved})。\n"
            f"advance 会把 NPC 产物 merge 进该仓库 main,会污染产品历史。\n"
            f"请用一次性沙箱(默认 data/sandbox-repo),或显式传一个空仓库目录。"
        )
    if not (resolved / ".git").is_dir():
        resolved.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main", str(resolved)], check=True)
        # 沙箱本地身份,避免依赖全局 git config
        subprocess.run(["git", "-C", str(resolved), "config", "user.name", "TerraWorks Sandbox"], check=True)
        subprocess.run(["git", "-C", str(resolved), "config", "user.email", "sandbox@terraworks.local"], check=True)
        (resolved / "README.md").write_text(
            "# TerraWorks 沙箱仓库\n\nNPC 在此 demo,产物 merge 进本仓库 main。可随时删除重建。\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(resolved), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(resolved), "commit", "-m", "chore: 沙箱初始化"], check=True)
        print(f"[sandbox] 已初始化沙箱仓库:{resolved}")
    return resolved


def main() -> None:
    ap = argparse.ArgumentParser(description="TerraWorks View 事件服务(读 + 写/编排,ADR-021/022/023)")
    ap.add_argument("--db", default="data/session.db", help="SQLite 路径")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--poll-interval", type=float, default=0.3, help="Live 轮询间隔(秒)")
    ap.add_argument("--repo-root", default="data/sandbox-repo",
                    help="NPC 产物 merge 目标仓库——必须是一次性沙箱,不能是产品仓库(ADR-023)")
    ap.add_argument("--worktrees-base", default="data/worktrees", help="NPC worktree 基目录")
    args = ap.parse_args()
    repo_root = ensure_sandbox_repo(Path(args.repo_root))
    # 写端点会后台跑 advance(真实 LLM 需 TERRA_LLM_MODE=real + .env)
    uvicorn.run(
        create_app(args.db, poll_interval=args.poll_interval,
                   repo_root=repo_root, worktrees_base=Path(args.worktrees_base)),
        host=args.host, port=args.port,
    )


if __name__ == "__main__":
    main()
