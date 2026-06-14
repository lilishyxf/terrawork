"""Mock LLM client. Fixture-driven, no hard-coded responses.

启动时扫 harness/tests/fixtures/m12_*.json,从每份 fixture 的 reference_output
反向序列化出"LLM 应返回的 JSON",以 trigger.payload.text 为 key 建查表。
新增 fixture 自动产生新的 mock 响应,无需改本文件。
"""
import json
from pathlib import Path

_FIXTURES_DIR = Path(__file__).parent.parent / "tests" / "fixtures"


def _build_response_table() -> dict[str, str]:
    table = {}
    for path in _FIXTURES_DIR.glob("m12_*.json"):
        fx = json.loads(path.read_text(encoding="utf-8"))
        trigger_text = fx["trigger"]["payload"]["text"]
        ref = fx["reference_output"]
        raw_output = {
            "thinking": "\n\n".join(
                e["payload"]["text"] for e in ref if e["type"] == "guide_think"
            ),
            "tasks": [
                e["payload"]["task_card"]
                for e in ref if e["type"] == "guide_delegate"
            ],
        }
        table[trigger_text] = json.dumps(raw_output, ensure_ascii=False)
    return table


_RESPONSES = _build_response_table()


def complete(model: str, messages: list[dict], **kwargs) -> str:
    """从 messages 取最后一条 user content,子串匹配 trigger_text 命中预设响应。"""
    user_msg = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"),
        None,
    )
    if user_msg is None:
        raise ValueError("mock: no user message in messages list")
    for trigger_text, response in _RESPONSES.items():
        if trigger_text in user_msg:
            return response
    raise ValueError(
        f"mock: no preset response for user message: {user_msg[:120]!r}"
    )
