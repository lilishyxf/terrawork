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


# 完成规约(收敛护栏):所有 builder 共用,附在角色 system 末尾。防止反复 bash 撞迭代上限。
_COMPLETION_PROTOCOL = """

## ⏱ 完成规约(必须遵守,关乎收敛)
- 你的工具调用次数**有预算上限**,超了会判失败。务必高效。
- 标准路径:`read` 必要上下文 → `write` 实现 → **至多一次** `bash` 做必要自检 → **停手**。
- **完成 = 不再调用任何工具**,只回一段简短文字总结产出。系统据此判定你完工。
- **严禁反复 `bash` 自测/打印/查看**。`bash` 不是验收闸(那是验证者的事);
  本地绿不代表完成,别为"再确认一下"而循环跑命令。
- 实现写完且自检无明显错误,就立刻停手交付,把验收交给后续 verifier/reviewer。
"""


def assemble_context_for_npc(role_name: str, task_card: dict) -> list[dict]:
    """构造 NPC 执行所需的 LLM messages 列表(M1.3 最小版本)。

    system = 角色 body + 完成规约(收敛护栏);user = 任务卡渲染。
    """
    return [
        {"role": "system", "content": _load_role_body(role_name) + _COMPLETION_PROTOCOL},
        {"role": "user", "content": _render_task_card(task_card)},
    ]


# --- M2.1 审查 context 物理隔离(铁律③ / §5 可见性矩阵 / ADR-002) ---

# 审查 NPC 只看"事实",不看"叙述"。§5 矩阵:审查 NPC 可见 tool_intent/done(代码与文件
# 事实)、verify_run、review_verdict、user_command(任务相关);**guide_think / npc_think
# 物理隔离(❌)**。此白名单由代码硬过滤强制,不允许 prompt 层绕过。
_REVIEWER_VISIBLE_TYPES = frozenset(
    {"user_command", "tool_intent", "tool_done", "verify_run", "review_verdict"}
)

# 永不进入审查 context 的"叙述"类型(显式列出,作隔离断言锚点)。
_NARRATIVE_TYPES = frozenset({"guide_think", "npc_think"})


def filter_events_for_review(events: list[dict]) -> list[dict]:
    """按 §5 可见性矩阵硬过滤出审查 NPC 可见的事实事件。

    只保留 _REVIEWER_VISIBLE_TYPES;guide_think / npc_think 等叙述类型一律剔除(铁律③)。
    """
    return [e for e in events if e["type"] in _REVIEWER_VISIBLE_TYPES]


def _render_facts(events: list[dict]) -> str:
    """把过滤后的事实事件渲染为审查者可读文本(仅事实,无推理)。"""
    lines = []
    for e in events:
        p = e["payload"]
        if e["type"] == "tool_intent":
            lines.append(f"- [{e['agent']}] 调用 {p.get('tool')} {p.get('params', {})}")
        elif e["type"] == "tool_done":
            lines.append(
                f"- [{e['agent']}] {p.get('tool')} 完成 file={p.get('file', '')} "
                f"status={p.get('status')} hash={(p.get('hash') or '')[:12]}"
            )
        elif e["type"] == "verify_run":
            lines.append(
                f"- [验证] `{p.get('command')}` exit_code={p.get('exit_code')} passed={p.get('passed')}"
            )
        elif e["type"] == "review_verdict":
            lines.append(f"- [既往结论] {p.get('verdict')} {p.get('notes', '')}")
        elif e["type"] == "user_command":
            lines.append(f"- [用户指令] {p.get('text', '')}")
    return "\n".join(lines) if lines else "(无事实事件)"


def assemble_review_context(
    task_card: dict, session_events: list[dict], *, role_name: str = "tailor"
) -> list[dict]:
    """为审查 NPC 装配 context:system(角色) + 任务卡 + **物理隔离后的事实历史**。

    制作 NPC 的 npc_think、Guide 的 guide_think 在此被代码硬过滤剔除(铁律③ / ADR-002),
    审查者拿不到任何"叙述",只拿"事实"(代码/工具/测试结果)。
    """
    facts = filter_events_for_review(session_events)
    user = (
        _render_task_card(task_card)
        + "\n\n## 待审查的事实(仅代码/工具/测试事实,制作者推理已被物理隔离)\n"
        + _render_facts(facts)
    )
    return [
        {"role": "system", "content": _load_role_body(role_name)},
        {"role": "user", "content": user},
    ]
