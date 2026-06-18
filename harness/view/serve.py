"""手动启动 View 事件服务(M3-2/3)。

    python -m harness.view.serve --db data/session.db --port 8000

配合前端:另开终端 `npm run dev`(Vite :5173),页面连本服务的 :8000。
"""
import argparse

import uvicorn

from harness.view.server import create_app


def main() -> None:
    ap = argparse.ArgumentParser(description="TerraWorks View 事件服务(只读)")
    ap.add_argument("--db", default="data/session.db", help="SQLite 路径")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--poll-interval", type=float, default=0.3, help="Live 轮询间隔(秒)")
    args = ap.parse_args()
    uvicorn.run(create_app(args.db, poll_interval=args.poll_interval), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
