"""LLM client factory. Switches between mock (default) and real via env."""
import os


def resolve_model(default):
    """模型选择(全局覆盖):TERRA_MODEL_OVERRIDE 非空则覆盖角色默认模型。

    用户在 UI 选了模型 → 服务端设此 env → 向导/builder/reviewer 全用它;
    留空(未选)→ 各角色用 roles/*.md 里的默认 model。
    """
    override = os.environ.get("TERRA_MODEL_OVERRIDE", "").strip()
    return override or default


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
