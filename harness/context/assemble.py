"""assembleContext minimal version (ARCHITECTURE.md §8).

M1.3-3 范围:
- 系统层:roles/<role_name>.md body
- 任务层:task_card 渲染为人类可读 user message

不含(留 M1.4 / M2 / M3):
- 历史层(本 NPC 过往事件)
- L2/L3 分层注入(本任务相关文件历史 / query_session 工具)
"""
from pathlib import Path
import yaml


_PROJECT_ROOT = Path(__file__).parent.parent.parent


def load_role_frontmatter(role_name: str) -> dict:
    """Parse and return the YAML frontmatter dict from roles/{role_name}.md.

    Used by executor to read role's `model` field for LLM dispatch.
    """
    role_file = _PROJECT_ROOT / "roles" / f"{role_name}.md"
    if not role_file.exists():
        raise FileNotFoundError(f"role file not found: {role_file}")
    text = role_file.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{role_file} 缺 YAML frontmatter")
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        raise ValueError(f"{role_file} frontmatter 未闭合")
    return yaml.safe_load(parts[1]) or {}


def _load_role_body(role_name: str) -> str:
    """读 roles/<role_name>.md,提取 body(system prompt 正文,跳过 frontmatter)。"""
    role_file = _PROJECT_ROOT / "roles" / f"{role_name}.md"
    if not role_file.exists():
        raise FileNotFoundError(f"role file not found: {role_file}")
    text = role_file.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"{role_file} 缺 YAML frontmatter")
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        raise ValueError(f"{role_file} frontmatter 未闭合")
    return parts[2].lstrip()


def _render_task_card(task_card: dict) -> str:
    """把 task_card dict 渲染为人类可读的 user message。"""
    lines = [
        f"# 任务:{task_card['task_id']}",
        "",
        "## 目标",
        task_card["objective"],
        "",
        "## 输出格式",
        task_card.get("output_format", "(未指定)"),
        "",
        f"## 允许的工具",
        ", ".join(task_card.get("allowed_tools", [])),
        "",
    ]
    boundaries = task_card.get("boundaries", [])
    if boundaries:
        lines.append("## 必须遵守的边界")
        for b in boundaries:
            lines.append(f"- {b}")
        lines.append("")
    verifications = task_card.get("verification", [])
    if verifications:
        lines.append("## 验证条件")
        for v in verifications:
            if v["type"] == "machine_verifiable":
                lines.append(f"- 跑 `{v['command']}`,期望 {v.get('expected', {})}")
            elif v["type"] == "hitl_escalation":
                lines.append(
                    f"- 人工验收:{v.get('reason', '')} - {v.get('acceptance_prompt', '')}"
                )
        lines.append("")
    return "\n".join(lines)


def assemble_context_for_npc(role_name: str, task_card: dict) -> list[dict]:
    """构造 NPC 执行所需的 LLM messages 列表(M1.3 最小版本)。

    Returns:
        [{"role": "system", "content": <roles/{role_name}.md body>},
         {"role": "user", "content": <task_card 渲染>}]
    """
    return [
        {"role": "system", "content": _load_role_body(role_name)},
        {"role": "user", "content": _render_task_card(task_card)},
    ]
