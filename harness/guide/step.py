"""Guide step function — the orchestrator's pure-function core (ADR-009).

ARCHITECTURE.md §1 铁律①:Harness 无状态。Guide 实现为纯函数,
所有状态从 session_events 读、所有产出通过返回值给出。

流程:
  trigger_event → build messages → LLM completion → parse → validate → events
  若 parse 或 validate 失败,把错误反馈进 messages 重试。
  重试用尽返回单一 hitl_request 事件,不抛异常。
"""
from datetime import datetime
from jsonschema.exceptions import ValidationError

from harness.guide.prompt import build_messages, get_guide_model
from harness.guide.parser import parse_llm_output, ParseError
from harness.session.schema import (
    validate_event, validate_task_card, validate_verification, SchemaError,
)
from harness.llm import get_llm_client


def guide_step(
    session_events: list[dict],
    trigger_event: dict,
    llm_client=None,
    *,
    model: str | None = None,
    max_retries: int = 3,
) -> list[dict]:
    """
    Args:
        session_events: 当前 session 的全部事件(包含 trigger)
        trigger_event: 触发本次推理的事件(通常是 user_command)
        llm_client: LLM 客户端模块;None 时从 get_llm_client() 工厂获取
        model: LLM 模型名;None 时从 roles/guide.md 读取
        max_retries: ParseError/SchemaError 时重试上限,默认 3

    Returns:
        新事件列表。成功:[guide_think, guide_delegate, ...];
        重试用尽:[hitl_request](单一事件,叫人来)。

    Raises:
        其他异常(LLM 网络错误等)向上传播,不静默重试。
        只有 ParseError / SchemaError(及 jsonschema ValidationError)进入重试循环。
    """
    if llm_client is None:
        llm_client = get_llm_client()
    if model is None:
        model = get_guide_model()

    session_id = trigger_event["session_id"]
    trigger_eid = trigger_event["event_id"]
    starting_eid = _next_event_id(session_events)
    ts_iso = datetime.utcnow().isoformat() + "Z"

    messages = build_messages(trigger_event)
    last_error = None
    last_raw = None

    for attempt in range(max_retries):
        try:
            raw_json = llm_client.complete(model=model, messages=messages)
            last_raw = raw_json

            candidate_events = parse_llm_output(
                raw_json=raw_json,
                session_id=session_id,
                starting_event_id=starting_eid,
                trigger_event_id=trigger_eid,
                ts_iso=ts_iso,
            )

            # Schema 校验门(ADR-007)。validate_* 失败抛 SchemaError。
            for e in candidate_events:
                validate_event(e)
                if e["type"] == "guide_delegate":
                    tc = e["payload"]["task_card"]
                    validate_task_card(tc)
                    for v in tc["verification"]:
                        validate_verification(v)

            return candidate_events

        except (ParseError, SchemaError, ValidationError) as e:
            last_error = f"{type(e).__name__}: {e}"
            if attempt < max_retries - 1:
                # 重新构造 messages 而非 append,保持 prompt 长度可控
                messages = build_messages(trigger_event) + [
                    {"role": "assistant", "content": last_raw or ""},
                    {"role": "user", "content":
                        f"上一次输出未通过校验:\n{last_error}\n"
                        f"请严格按 system 提示中的 JSON 输出格式重新生成。"},
                ]

    # 重试用尽,返回 hitl_request 兜底。
    # 字段命名以 docs/contracts/events.schema.json 中 p_hitl_request 为准:
    #   additionalProperties:false,required [reason, question],仅允许 task_id/reason/question。
    #   故诊断信息(末次错误)折进 reason,不另立 last_error/last_raw_output 字段。
    return [{
        "event_id": starting_eid,
        "session_id": session_id,
        "ts": ts_iso,
        "agent": "guide",
        "type": "hitl_request",
        "parent_event_id": trigger_eid,
        "payload": {
            "reason": (
                f"Guide 分解 {max_retries} 次后产出仍未通过 schema 校验;"
                f"末次错误:{last_error}"
            ),
            "question": "请人工接手该指令的任务分解,或修正后重试。",
        }
    }]


def _next_event_id(session_events: list[dict]) -> int:
    """从 session_events 找最大 event_id 并 +1。空 list 时返回 2。"""
    if not session_events:
        return 2
    return max(e["event_id"] for e in session_events) + 1
