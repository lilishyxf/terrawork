"""Session 层（事件日志地基，M1.1）。

公开 API：
    SessionStore       —— append-only 事件日志（SQLite WAL）
    validate_event     —— events.schema.json 校验门
    SchemaError / CausalityError / AppendOnlyError —— 拒绝写入的异常类型
"""
from .schema import SchemaError, validate_event
from .store import AppendOnlyError, CausalityError, SessionStore

__all__ = [
    "SessionStore",
    "validate_event",
    "SchemaError",
    "CausalityError",
    "AppendOnlyError",
]
