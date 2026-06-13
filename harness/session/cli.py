"""Session 层手动验收 CLI（M1.1）。

子命令：
    init                建库（WAL + append-only 触发器）
    insert <file>       从 JSON（单条或数组）灌入事件；--remap 把 fixture 内的
                        event_id/parent_event_id 重映射为实际自增 id；
                        --continue-on-error 遇非法事件不中断、逐条报告 OK/REJECTED
    dump                按 parent_event_id 打印因果树森林
    query               按 --type/--agent/--task-id/--since/--until 过滤打印

用法示例：
    python -m harness.session.cli --db data/session.db init
    python -m harness.session.cli --db data/session.db insert harness/tests/fixtures/login_chain.json --remap
    python -m harness.session.cli --db data/session.db dump
    python -m harness.session.cli --db data/session.db insert harness/tests/fixtures/invalid_events.json --continue-on-error
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .store import CausalityError, SchemaError, SessionStore


def _load_events(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def _cmd_init(store: SessionStore, args: argparse.Namespace) -> int:
    print(f"已初始化 Session 日志：{store.db_path}（WAL + append-only 触发器）")
    return 0


def _cmd_insert(store: SessionStore, args: argparse.Namespace) -> int:
    events = _load_events(args.file)
    idmap: dict = {}
    ok = rejected = 0
    for raw in events:
        old_id = raw.get("event_id")
        old_parent = raw.get("parent_event_id")
        new_parent = None if old_parent is None else (
            idmap.get(old_parent, old_parent) if args.remap else old_parent
        )
        try:
            ev = store.append_event(
                agent=raw["agent"],
                type=raw["type"],
                payload=raw["payload"],
                parent_event_id=new_parent,
                session_id=raw.get("session_id"),
                ts=raw.get("ts"),
            )
            if old_id is not None:
                idmap[old_id] = ev["event_id"]
            ok += 1
            print(f"OK       #{ev['event_id']:<4} {ev['type']:<16} (fixture #{old_id})")
        except (SchemaError, CausalityError) as exc:
            rejected += 1
            label = type(exc).__name__
            print(f"REJECTED fixture #{old_id} {raw.get('type', '?'):<16} [{label}] {exc}")
            if not args.continue_on_error:
                print("（未加 --continue-on-error，中断）", file=sys.stderr)
                return 1
    print(f"\n汇总：落盘 {ok} 条，拒绝 {rejected} 条。")
    return 0


def _print_tree(node: dict, depth: int = 0) -> None:
    pad = "  " * depth + ("└─ " if depth else "")
    payload = node["payload"]
    hint = (
        payload.get("text")
        or payload.get("verdict")
        or payload.get("tool")
        or payload.get("command")
        or payload.get("result")
        or payload.get("assignee")
        or payload.get("kind")
        or ""
    )
    hint = (str(hint)[:40] + "…") if len(str(hint)) > 40 else str(hint)
    print(f"{pad}#{node['event_id']:<3} {node['type']:<16} [{node['agent']}] {hint}")
    for child in node["children"]:
        _print_tree(child, depth + 1)


def _cmd_dump(store: SessionStore, args: argparse.Namespace) -> int:
    forest = store.replay_forest(session_id=args.session)
    if not forest:
        print("（空）")
        return 0
    for root in forest:
        _print_tree(root)
    return 0


def _cmd_query(store: SessionStore, args: argparse.Namespace) -> int:
    rows = store.query_session(
        session_id=args.session,
        type=args.type,
        agent=args.agent,
        task_id=args.task_id,
        since_event_id=args.since,
        until_event_id=args.until,
    )
    for e in rows:
        print(f"#{e['event_id']:<4} {e['type']:<16} parent={e['parent_event_id']} "
              f"[{e['agent']}] {json.dumps(e['payload'], ensure_ascii=False)}")
    print(f"\n命中 {len(rows)} 条。")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="session-cli", description="TerraWorks Session 层验收 CLI")
    p.add_argument("--db", default="data/session.db", help="SQLite 文件路径")
    p.add_argument("--session", default=None, help="按 session_id 过滤（dump/query）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="建库")

    sp = sub.add_parser("insert", help="灌入事件 JSON（单条或数组）")
    sp.add_argument("file")
    sp.add_argument("--remap", action="store_true", help="重映射 fixture id 到实际自增 id")
    sp.add_argument("--continue-on-error", action="store_true", help="遇非法事件不中断")

    sub.add_parser("dump", help="按因果链打印事件树")

    sq = sub.add_parser("query", help="过滤查询")
    sq.add_argument("--type")
    sq.add_argument("--agent")
    sq.add_argument("--task-id", dest="task_id")
    sq.add_argument("--since", type=int)
    sq.add_argument("--until", type=int)

    args = p.parse_args(argv)
    handlers = {
        "init": _cmd_init,
        "insert": _cmd_insert,
        "dump": _cmd_dump,
        "query": _cmd_query,
    }
    sid = args.session or "default"
    with SessionStore(args.db, session_id=sid) as store:
        return handlers[args.cmd](store, args)


if __name__ == "__main__":
    raise SystemExit(main())
