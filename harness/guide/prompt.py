"""Build LLM messages from Guide role file + trigger event.

读 roles/guide.md 的 frontmatter (model) 与 body (system prompt),
与 trigger event 的 user_command 文本拼成 LiteLLM messages 格式。
"""
from pathlib import Path
import yaml

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_ROLES_DIR = _PROJECT_ROOT / "roles"
_GUIDE_ROLE_FILE = _ROLES_DIR / "guide.md"


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


def _builder_catalog() -> str:
    """扫 roles/,列出全部 role:builder 的专家目录(ADR-019 角色感知委派)。

    每行 `- <name>(<display_name>): <summary>`。向导据此为每张 builder 卡选 assignee_specialty。
    角色即插件:新增 roles/<name>.md(role:builder) 自动进目录,无需改代码。
    """
    rows = []
    for f in sorted(_ROLES_DIR.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            continue
        parts = text.split("---\n", 2)
        if len(parts) < 3:
            continue
        fm = yaml.safe_load(parts[1]) or {}
        if fm.get("role") != "builder":
            continue
        name = fm.get("name", f.stem)
        disp = fm.get("display_name", name)
        summary = fm.get("summary", "")
        rows.append(f"- {name}({disp}): {summary}".rstrip(": ").rstrip())
    return "\n".join(rows)


def build_messages(trigger_event: dict) -> list[dict]:
    """从 trigger user_command 事件构造 LLM messages 列表。

    system 内容 = guide.md body + 动态注入的 builder 专家目录(ADR-019),
    向导据目录为每张 builder 卡填 assignee_specialty(说不清就省略 → 默认 merchant)。

    Returns:
        [{role: "system", content: <guide.md body + 专家目录>},
         {role: "user", content: <trigger.payload.text>}]
    """
    _, system_prompt = _load_role_file()
    catalog = _builder_catalog()
    if catalog:
        system_prompt = (
            system_prompt
            + "\n\n## 可用 builder 专家目录(角色感知委派)\n"
            + "为每张 builder 卡的 `assignee_specialty` 选下面**最合适**的 name;"
            + "说不清或通用任务就**省略该字段**(默认 merchant)。\n\n"
            + catalog
        )
    user_text = trigger_event["payload"]["text"]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
