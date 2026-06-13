"""从 login_chain.json 重建版本化 fixture：data/fixtures/m11_login_chain.db。

契约：db 是二进制产物，不能光 commit 一个 db，必须有"从 json 重建它"的脚本。
这样 M1.2 拿它当输入素材时，db 永远可由 login_chain.json 复现。

硬性约束（M1.1 设计不变量）：必须经 SessionStore.append_event 写入，
所有事件都过 events.schema.json 校验门——**不允许**直接 SQL INSERT 绕过校验。
rebuild 脚本不能开后门。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 以脚本路径（而非 -m）运行，故手动把 repo 根加入 sys.path。
# fixtures -> tests -> harness -> repo_root
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from harness.session import SessionStore  # noqa: E402

FIXTURE_JSON = Path(__file__).with_name("login_chain.json")
DB_PATH = REPO_ROOT / "data" / "fixtures" / "m11_login_chain.db"


def rebuild() -> int:
    events = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))

    # 删除旧 db 及 WAL/SHM 边车（如存在），确保是干净重建。
    for p in (DB_PATH,
              DB_PATH.with_name(DB_PATH.name + "-wal"),
              DB_PATH.with_name(DB_PATH.name + "-shm")):
        if p.exists():
            p.unlink()

    store = SessionStore(DB_PATH, session_id="s-login-demo")
    idmap: dict[int, int] = {}  # fixture event_id -> 实际自增 event_id
    try:
        for raw in events:
            old_parent = raw.get("parent_event_id")
            # 父事件按因果顺序必已写入；缺失则 KeyError 主动暴露问题。
            new_parent = None if old_parent is None else idmap[old_parent]
            ev = store.append_event(            # 经 schema 校验门，非直写 SQL
                agent=raw["agent"],
                type=raw["type"],
                payload=raw["payload"],
                parent_event_id=new_parent,
                session_id=raw.get("session_id"),
                ts=raw.get("ts"),
            )
            idmap[raw["event_id"]] = ev["event_id"]
    finally:
        store.close()

    print(f"rebuilt: data/fixtures/m11_login_chain.db, {len(events)} events")
    return len(events)


if __name__ == "__main__":
    rebuild()
