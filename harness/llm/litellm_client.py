"""Real LLM provider via LiteLLM. ADR-009.

强制 JSON 输出格式;失败时不静默吞,raise 由上层决定重试。

网络韧性:对瞬时错误(网络抖断、不完整响应、5xx、超时)做指数退避重试。
不稳定网络(如手机热点)下,单次调用的短暂中断由此自愈,不再炸穿整条 advance。
"""
import time

import litellm

_MAX_TRIES = 4          # 总尝试次数(1 次 + 3 次重试)
_BACKOFF = 2.0          # 退避基数(秒):2, 4, 6 ...
_TIMEOUT = 120          # 单次调用超时(秒),避免网络挂死时无限等待


def _with_retry(call):
    """对瞬时异常退避重试;重试用尽后抛出最后一次异常,交由上层(resilience/自愈)处理。"""
    last = None
    for attempt in range(_MAX_TRIES):
        try:
            return call()
        except Exception as e:  # litellm 各类瞬时错误(InternalServerError/Timeout/APIConnection 等)
            last = e
            if attempt < _MAX_TRIES - 1:
                time.sleep(_BACKOFF * (attempt + 1))
    raise last


def complete(model: str, messages: list[dict], **kwargs) -> str:
    """同步调 LiteLLM,返回 JSON 字符串(由 response_format 约束)。瞬时错误自动重试。"""
    kwargs.setdefault("timeout", _TIMEOUT)
    response = _with_retry(lambda: litellm.completion(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        **kwargs,
    ))
    return response.choices[0].message.content


def complete_with_tools(model: str, messages: list[dict], tools: list[dict], **kwargs):
    """LiteLLM completion with tool calling.瞬时错误自动重试。

    Unlike `complete()`, does NOT set response_format (incompatible with tool calling).
    Returns the FULL message object (response.choices[0].message), not just .content.

    Caller is responsible for:
    - parsing message.tool_calls (each tc has .id, .function.name, .function.arguments JSON string)
    - executing tools and appending tool results as messages with role='tool' + tool_call_id
    - re-calling with updated messages until message.tool_calls is empty/None
    """
    kwargs.setdefault("timeout", _TIMEOUT)
    response = _with_retry(lambda: litellm.completion(
        model=model,
        messages=messages,
        tools=tools,
        **kwargs,
    ))
    return response.choices[0].message
