"""Build LLM messages from Guide role file + trigger event.

读 roles/guide.md 的 frontmatter (model) 与 body (system prompt),
与 trigger event 的 user_command 文本拼成 LiteLLM messages 格式。
"""
from pathlib import Path
import yaml

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_GUIDE_ROLE_FILE = _PROJECT_ROOT / "roles" / "guide.md"


def _load_role_file() -> tuple[dict, str]:
    """解析 roles/guide.md,返回 (frontmatter_dict, body_str)。"""
    text = _GUIDE_ROLE_FILE.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{_GUIDE_ROLE_FILE} 缺少 YAML frontmatter")
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        raise ValueError(f"{_GUIDE_ROLE_FILE} frontmatter 未闭合")
    frontmatter = yaml.safe_load(parts[1])
    body = parts[2].lstrip()
    return frontmatter, body


def get_guide_model() -> str:
    """Returns roles/guide.md 的 frontmatter.model 字段(ADR-009)。"""
    fm, _ = _load_role_file()
    model = fm.get("model")
    if not model:
        raise ValueError(f"{_GUIDE_ROLE_FILE} frontmatter 缺 model 字段")
    return model


def build_messages(trigger_event: dict) -> list[dict]:
    """从 trigger user_command 事件构造 LLM messages 列表。

    Returns:
        [{role: "system", content: <guide.md body>},
         {role: "user", content: <trigger.payload.text>}]
    """
    _, system_prompt = _load_role_file()
    user_text = trigger_event["payload"]["text"]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
