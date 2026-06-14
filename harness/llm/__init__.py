"""LLM client factory. Switches between mock (default) and real via env."""
import os


def get_llm_client():
    """根据 TERRA_LLM_MODE 返回 mock_client 或 litellm_client(默认 mock)。"""
    mode = os.environ.get("TERRA_LLM_MODE", "mock").lower()
    if mode == "mock":
        from . import mock_client
        return mock_client
    elif mode == "real":
        from . import litellm_client
        return litellm_client
    else:
        raise ValueError(f"Unknown TERRA_LLM_MODE: {mode!r}")
