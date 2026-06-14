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
