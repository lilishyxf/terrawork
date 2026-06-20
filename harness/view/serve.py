"""手动启动 View 事件服务(M3-2/3)。

    python -m harness.view.serve --db data/session.db --port 8000

配合前端:另开终端 `npm run dev`(Vite :5173),页面连本服务的 :8000。
"""
import argparse
from pathlib import Path

import uvicorn

from harness.view.server import create_app


def main() -> None:
    ap = argparse.ArgumentParser(description="TerraWorks View 事件服务(读 + 写/编排,ADR-021/022)")
    ap.add_argument("--db", default="data/session.db", help="SQLite 路径")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--poll-interval", type=float, default=0.3, help="Live 轮询间隔(秒)")
    ap.add_argument("--repo-root", default=".", help="主仓库根(advance merge 到 main)")
    ap.add_argument("--worktrees-base", default="data/worktrees", help="NPC worktree 基目录")
    args = ap.parse_args()
    # 写端点会后台跑 advance(真实 LLM 需 TERRA_LLM_MODE=real + .env)
    uvicorn.run(
        create_app(args.db, poll_interval=args.poll_interval,
                   repo_root=Path(args.repo_root), worktrees_base=Path(args.worktrees_base)),
        host=args.host, port=args.port,
    )


if __name__ == "__main__":
    main()
