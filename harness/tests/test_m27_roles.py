"""M2.7-2 角色库注册表一致性(ADR-019)。

锁:roles/ 下每个角色文件都能被 load_role_frontmatter 加载、name==文件名、role 合法、
有 model 键;8 个 builder 专长齐全且 specialty==name;appsec 是 reviewer;
roles/ 内无残留的原始 agency-agents 源文件(缺 role 字段者)。
"""
from pathlib import Path

import pytest

from harness.context.assemble import load_role_frontmatter

ROLES_DIR = Path(__file__).resolve().parents[2] / "roles"
VALID_ROLES = {"builder", "reviewer", "verifier", "orchestrator"}

# ADR-019 锁定的 8 个 builder 专长(M2.7 选甲:含 mobile + desktop_shell)
BUILDER_SPECIALTIES = {
    "frontend", "backend", "database", "desktop_shell",
    "ai_engineer", "rapid_proto", "tech_writer", "mobile",
}


def _role_stems():
    return sorted(p.stem for p in ROLES_DIR.glob("*.md"))


def test_every_role_file_loads_and_is_consistent():
    stems = _role_stems()
    assert stems, "roles/ 不应为空"
    for stem in stems:
        fm = load_role_frontmatter(stem)
        # 缺 role 字段 = 原始源文件残留(未适配),不应出现在 roles/
        assert "role" in fm, f"{stem}.md 缺 role 字段(疑似未适配的源文件,应归档到 docs/role-sources/)"
        assert fm["role"] in VALID_ROLES, f"{stem}: role={fm['role']!r} 非法"
        assert fm.get("name") == stem, f"{stem}: name 应等于文件名,实际 {fm.get('name')!r}"
        assert "model" in fm, f"{stem}: 缺 model 键(可为 null)"


def test_eight_builder_specialties_present():
    """8 个 builder 专长齐全,均 role:builder 且 specialty==name。"""
    for spec in BUILDER_SPECIALTIES:
        fm = load_role_frontmatter(spec)
        assert fm["role"] == "builder", f"{spec}: 应为 builder,实际 {fm['role']}"
        assert fm.get("specialty") == spec, \
            f"{spec}: specialty 应==name,实际 {fm.get('specialty')!r}"
        assert "write" in fm.get("tools", []), f"{spec}: builder 应有 write 工具"


def test_two_reviewers_present():
    """tailor(代码审查)+ appsec(安全审查)均为 reviewer、只读。"""
    for rev in ("tailor", "appsec"):
        fm = load_role_frontmatter(rev)
        assert fm["role"] == "reviewer", f"{rev}: 应为 reviewer"
        assert fm.get("tools") == ["read"], f"{rev}: reviewer 应只读 tools:[read]"


def test_no_raw_source_files_in_roles():
    """roles/ 内不应残留原始 agency-agents 源文件(它们应在 docs/role-sources/)。"""
    leftover = [p.name for p in ROLES_DIR.glob("*.md")
                if p.stem.startswith(("engineering-", "security-", "design-", "gis-"))]
    assert leftover == [], f"原始源文件应归档,残留: {leftover}"
