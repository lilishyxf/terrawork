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
    """Returns roles/guide.md 的 frontmatter.model(ADR-009);TERRA_MODEL_OVERRIDE 可全局覆盖。"""
    from harness.llm import resolve_model
    fm, _ = _load_role_file()
    model = fm.get("model")
    if not model:
        raise ValueError(f"{_GUIDE_ROLE_FILE} frontmatter 缺 model 字段")
    return resolve_model(model)


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


def _state_summary(session_events: list[dict] | None) -> str:
    """把当前会话状态压成一段可读摘要,供向导回答"进展/为什么失败"这类提问(ADR-023 对话)。

    含:任务板(各卡状态+负责人)、最近错误(失败的 NPC 与原因)。空会话返回 ""。
    """
    if not session_events:
        return ""
    from harness.view.projection import project
    snap = project(session_events)
    parts: list[str] = []
    tb = snap.get("task_board") or {}
    if tb:
        parts.append("任务板:")
        for tid, slot in tb.items():
            who = f"(负责:{slot['builder']})" if slot.get("builder") else ""
            parts.append(f"  - {tid}:{slot['status']}{who}")
    errs = [e for e in session_events if e.get("type") == "error"]
    if errs:
        parts.append("最近错误:")
        for e in errs[-5:]:
            p = e.get("payload", {})
            who = p.get("agent_ref") or e.get("agent")
            parts.append(f"  - {who}:{str(p.get('message', ''))[:200]}")
    return "\n".join(parts)


def build_messages(trigger_event: dict, session_events: list[dict] | None = None) -> list[dict]:
    """从 trigger user_command 事件构造 LLM messages 列表。

    system 内容 = guide.md body + builder 专家目录(ADR-019) + 当前小镇实时状态(ADR-023:
    使向导能回答用户关于进展/失败的提问,而非只会套话)。

    Returns:
        [{role: "system", content: <guide.md body + 专家目录 + 实时状态>},
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
    summary = _state_summary(session_events)
    if summary:
        system_prompt = (
            system_prompt
            + "\n\n## 当前小镇实时状态(回答用户提问时据此如实、具体作答)\n"
            + "用户若问进展/为什么失败/某任务怎么样,**结合下面状态给出具体回答**,不要套话。\n\n"
            + summary
        )
    user_text = trigger_event["payload"]["text"]
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
