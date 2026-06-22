"""Parse LLM raw JSON output into guide_think + guide_delegate events.

LLM 按 roles/guide.md 约定的格式返回 {"thinking": str, "tasks": [task_card, ...]}。
Parser 把它转换成符合 events.schema.json 的事件列表。

event_id / ts / session_id / parent_event_id 由调用方注入,parser 自己不决定时间或编号。
"""
import json
from datetime import datetime


class ParseError(Exception):
    """LLM 输出解析失败:JSON 非法、结构不符、必需字段缺失。"""


def _extract_json_object(text: str):
    """从含杂质的文本里提取第一个**括号平衡**的 JSON 对象(应对 ```json 围栏、推理模型
    在 JSON 前后带说明文字)。提取不到返回 None。"""
    start = text.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        return None
    return None


def parse_llm_output(
    raw_json: str,
    session_id: str,
    starting_event_id: int,
    trigger_event_id: int,
    ts_iso: str | None = None,
) -> list[dict]:
    """把 LLM raw JSON 输出解析成 guide 事件列表。

    Args:
        raw_json: LLM 输出文本(应是单一 JSON 对象)
        session_id: 复制到所有产出事件
        starting_event_id: 第一个产出事件的 event_id(后续递增)
        trigger_event_id: 父事件 ID(guide_think 链回它)
        ts_iso: 产出事件的时间戳;None 时用当前 UTC

    Returns:
        [guide_think_event, guide_delegate_event_1, ..., guide_delegate_event_N]
        非任务输入(问候/闲聊/提问)时 tasks 可为空数组 → 仅返回 [guide_think],
        thinking 即向导对用户的回复(ADR-023 对话出口)。

    Raises:
        ParseError: JSON 解析失败、缺 thinking/tasks 字段、tasks 非数组。
    """
    if ts_iso is None:
        ts_iso = datetime.utcnow().isoformat() + "Z"

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        # 容错:推理模型/带 markdown 围栏时,JSON 前后可能有杂质 → 提取最外层 {...} 再试
        data = _extract_json_object(raw_json)
        if data is None:
            raise ParseError(f"LLM 输出非合法 JSON(且无法提取 JSON 对象): {raw_json[:200]!r}")

    if not isinstance(data, dict):
        raise ParseError(f"LLM 输出顶层必须是对象,实际 {type(data).__name__}")

    thinking = data.get("thinking")
    tasks = data.get("tasks")

    if not isinstance(thinking, str) or not thinking.strip():
        raise ParseError("LLM 输出缺 'thinking' 字段或为空字符串")
    if not isinstance(tasks, list):
        raise ParseError(f"LLM 输出 'tasks' 必须为数组,实际 {type(tasks).__name__}")
    # tasks 为空 = 非任务输入(问候/闲聊/提问),合法:仅产出 guide_think 作为回复(ADR-023)

    # 第一个事件:guide_think,parent 链回 trigger
    eid = starting_event_id
    think_event = {
        "event_id": eid,
        "session_id": session_id,
        "ts": ts_iso,
        "agent": "guide",
        "type": "guide_think",
        "parent_event_id": trigger_event_id,
        "payload": {"text": thinking.strip()},
    }
    events = [think_event]

    # 后续事件:每个 task 一条 guide_delegate,parent 链回 think
    think_eid = eid
    for task in tasks:
        eid += 1
        if not isinstance(task, dict):
            raise ParseError(
                f"tasks[{eid - think_eid - 1}] 必须为对象,实际 {type(task).__name__}"
            )
        delegate_event = {
            "event_id": eid,
            "session_id": session_id,
            "ts": ts_iso,
            "agent": "guide",
            "type": "guide_delegate",
            "parent_event_id": think_eid,
            "payload": {"task_card": task},
        }
        events.append(delegate_event)

    return events
