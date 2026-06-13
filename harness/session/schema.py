"""Session 层事件校验门（M1.1）。

所有写入 Session 日志的入口都必须先过 ``validate_event()``；校验失败即拒绝落盘
（ARCHITECTURE 第 4 节；铁律：仅追加 + 入口强校验）。校验直接以
``docs/contracts/events.schema.json`` 为唯一事实源，跨文件 ``$ref``
（task_card / verification）通过 referencing.Registry 解析。

校验器依赖策略（用户决策，M1.1）：**jsonschema 为主 + 启动期自检**——
缺依赖时给出清晰报错与安装指引，绝不静默降级到手写校验。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# --- 启动期自检：依赖缺失立即报错并给安装指引，不降级 ---
try:
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
except ModuleNotFoundError as exc:  # pragma: no cover - 仅在环境缺依赖时触发
    raise ModuleNotFoundError(
        "Session 层事件校验依赖 jsonschema + referencing，但未检测到。\n"
        "请在 harness 运行环境安装：\n"
        "    pip install -r harness/requirements.txt\n"
        "或：pip install 'jsonschema>=4.18' 'referencing>=0.30'\n"
        f"原始 ImportError：{exc}"
    ) from exc


#: 契约 schema 目录（repo_root/docs/contracts）。本文件位于 harness/session/。
CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "docs" / "contracts"

EVENTS_SCHEMA_ID = "https://terraworks.local/contracts/events.schema.json"

#: 需载入 Registry 的全部契约文件（events 通过 $ref 依赖后两者）。
_SCHEMA_FILES = (
    "events.schema.json",
    "task_card.schema.json",
    "verification.schema.json",
)


class SchemaError(ValueError):
    """事件未通过 events.schema.json 校验。携带全部具体错误信息。"""


@lru_cache(maxsize=1)
def _registry() -> "Registry":
    resources = []
    for name in _SCHEMA_FILES:
        path = CONTRACTS_DIR / name
        if not path.exists():
            raise FileNotFoundError(f"缺少契约 schema：{path}")
        doc = json.loads(path.read_text(encoding="utf-8"))
        resources.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=1)
def _event_validator() -> "Draft202012Validator":
    events_doc = json.loads(
        (CONTRACTS_DIR / "events.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(events_doc)  # 启动期再核 schema 本身合法
    return Draft202012Validator(events_doc, registry=_registry())


def validate_event(event: dict) -> None:
    """按 events.schema.json 校验单条完整事件。

    通过则静默返回；否则抛 :class:`SchemaError`，消息含全部字段级错误。
    """
    errors = sorted(_event_validator().iter_errors(event), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in errors
        )
        raise SchemaError(detail)
