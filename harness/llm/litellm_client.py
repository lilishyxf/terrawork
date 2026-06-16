"""Real LLM provider via LiteLLM. ADR-009.

强制 JSON 输出格式;失败时不静默吞,raise 由上层决定重试。
"""
import litellm


def complete(model: str, messages: list[dict], **kwargs) -> str:
    """同步调 LiteLLM,返回 JSON 字符串(由 response_format 约束)。"""
    response = litellm.completion(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        **kwargs,
    )
    return response.choices[0].message.content


def complete_with_tools(model: str, messages: list[dict], tools: list[dict], **kwargs):
    """LiteLLM completion with tool calling.

    Unlike `complete()`, does NOT set response_format (incompatible with tool calling).
    Returns the FULL message object (response.choices[0].message), not just .content.

    Caller is responsible for:
    - parsing message.tool_calls (each tc has .id, .function.name, .function.arguments JSON string)
    - executing tools and appending tool results as messages with role='tool' + tool_call_id
    - re-calling with updated messages until message.tool_calls is empty/None
    """
    response = litellm.completion(
        model=model,
        messages=messages,
        tools=tools,
        **kwargs,
    )
    return response.choices[0].message
