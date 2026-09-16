"""Skill 管理：service 级（列表/启停/删除/官方库/设置）+ Web 级流程。"""
from __future__ import annotations

from pathlib import Path

from app.hermes import skills_service as svc
from app.hermes.config_store import get_path, load_config
from tests.conftest import csrf_of


def _make_skill(home: Path, slug: str, *, name: str = "", description: str = "",
                managed: bool = False) -> Path:
    d = home / "skills" / slug
    d.mkdir(parents=True, exist_ok=True)
    fm = "---\n"
    fm += f"name: {name or slug}\n"
    fm += f"description: {description or slug + ' 的说明'}\n"
    if managed:
        fm += "managed-by: hermes-console\n"
    fm += "---\n\n# Body\n"
    (d / "SKILL.md").write_text(fm, encoding="utf-8")
    return d


def _seed_catalog(home: Path, rel: str, description: str = "official skill") -> Path:
    source = home / "hermes-agent" / "optional-skills" / rel
    source.mkdir(parents=True, exist_ok=True)
    (source / "SKILL.md").write_text(
        f"---\nname: {source.name}\ndescription: {description}\n---\n# x\n",
        encoding="utf-8")
    return source


# ---------------------------------------------------------------------------
# service 级
# ---------------------------------------------------------------------------

def test_installed_skills_and_frontmatter(hermes_home):
    _make_skill(hermes_home.home, "my-skill", name="My Skill", description="测试技能")
    _make_skill(hermes_home.home, "eng-skill", managed=True)
    (hermes_home.home / "skills" / "broken-dir").mkdir(parents=True)

    views = svc.installed_skills()
    by_slug = {v.slug: v for v in views}
    assert by_slug["my-skill"].display_name == "My Skill"
    assert by_slug["my-skill"].description == "测试技能"
    assert by_slug["my-skill"].origin == "user"
    assert by_slug["eng-skill"].origin == "engineering"
    assert by_slug["broken-dir"].broken is True
    # 工程托管排最前
    assert views[0].origin == "engineering"


def test_toggle_writes_disabled_list(hermes_home):
    _make_skill(hermes_home.home, "a")
    _make_skill(hermes_home.home, "b")
    svc.toggle_skill("a", False)
    svc.toggle_skill("b", False)
    svc.toggle_skill("b", True)
    disabled = get_path(load_config(hermes_home), "skills.disabled", None)
    assert disabled is not None
    assert list(disabled) == ["a"]
    assert svc.disabled_names() == ["a"]
    # 全部启用后清键，避免残留空 skills 节点
    svc.toggle_skill("a", True)
    assert get_path(load_config(hermes_home), "skills.disabled", None) is None

    import pytest
    with pytest.raises(svc.SkillError, match="必备"):
        svc.toggle_skill("hermes-agent", False)


def test_official_catalog_skill_recognized(hermes_home):
    _seed_catalog(hermes_home.home, "software-development/tdd", "Test-driven dev")
    _make_skill(hermes_home.home, "tdd")
    views = {v.slug: v for v in svc.installed_skills()}
    assert views["tdd"].origin == "catalog"


def test_remove_skill_rules(hermes_home):
    _make_skill(hermes_home.home, "user-skill")
    _make_skill(hermes_home.home, "eng-skill", managed=True)
    svc.toggle_skill("user-skill", False)

    import pytest
    with pytest.raises(svc.SkillError, match="工程规范"):
        svc.remove_skill("eng-skill")
    with pytest.raises(svc.SkillError, match="必备"):
        svc.remove_skill("hermes-agent")

    svc.remove_skill("user-skill")
    assert not (hermes_home.home / "skills" / "user-skill").exists()
    assert svc.disabled_names() == []  # 禁用名单同步清理


def test_catalog_scan_and_install(hermes_home):
    _seed_catalog(hermes_home.home, "software-development/tdd", "TDD")
    _seed_catalog(hermes_home.home, "web/scrape", "Scraping")
    entries = svc.catalog()
    assert [e.slug for e in entries] == ["tdd", "scrape"]
    assert entries[0].category == "software-development"

    slug = svc.install_from_catalog("software-development/tdd")
    assert slug == "tdd"
    assert (hermes_home.home / "skills" / "tdd" / "SKILL.md").exists()
    assert {e.slug for e in svc.catalog() if e.installed} == {"tdd"}

    import pytest
    with pytest.raises(svc.SkillError, match="已存在"):
        svc.install_from_catalog("software-development/tdd")
    with pytest.raises(svc.SkillError, match="非法"):
        svc.install_from_catalog("../../etc")
    with pytest.raises(svc.SkillError, match="缺少"):
        rel = "software-development/empty"
        (hermes_home.home / "hermes-agent" / "optional-skills" / rel).mkdir(parents=True)
        svc.install_from_catalog(rel)


def test_settings_roundtrip(hermes_home):
    s = svc.get_settings()
    assert s.template_vars is True and s.inline_shell is False

    s.external_dirs = ["~/.agents/skills", "/shared/team"]
    s.inline_shell = True
    s.inline_shell_timeout = 30
    s.guard_agent_created = True
    svc.save_settings(s)

    node = get_path(load_config(hermes_home), "skills")
    assert list(node["external_dirs"]) == ["~/.agents/skills", "/shared/team"]
    assert node["inline_shell"] is True and node["inline_shell_timeout"] == 30
    assert node["guard_agent_created"] is True
    assert "template_vars" not in node  # 默认值不落键

    s2 = svc.get_settings()
    assert s2.external_dirs == ["~/.agents/skills", "/shared/team"]
    assert s2.inline_shell and s2.inline_shell_timeout == 30

    # external_dirs 容错：官方语义是 list，但容忍手写成 JSON 字符串
    hermes_home.config_yaml.write_text(
        "skills:\n  external_dirs: '[\"~/.x\"]'\n", encoding="utf-8")
    assert svc.get_settings().external_dirs == ["~/.x"]

    import pytest
    s3 = svc.get_settings()
    s3.inline_shell_timeout = 999
    with pytest.raises(svc.SkillError, match="1-300"):
        svc.save_settings(s3)


def test_untrust_project_dir(hermes_home):
    hermes_home.config_yaml.write_text(
        "skills:\n"
        "  trusted_project_dirs:\n"
        "    - /home/u/proj-a\n"
        "    - /home/u/proj-b\n", encoding="utf-8")
    svc.untrust_project_dir("/home/u/proj-a")
    assert get_path(load_config(hermes_home), "skills.trusted_project_dirs") == [
        "/home/u/proj-b"]

    import pytest
    with pytest.raises(svc.SkillError, match="不存在"):
        svc.untrust_project_dir("/nowhere")


# ---------------------------------------------------------------------------
# Web 级
# ---------------------------------------------------------------------------

def test_web_skills_page_toggle_delete(logged_in, hermes_home):
    _make_skill(hermes_home.home, "web-skill", name="Web Skill")
    c = logged_in
    token = csrf_of(c)

    page = c.get("/skills")
    assert page.status_code == 200
    assert "Web Skill" in page.text

    # 禁用
    resp = c.post("/skills/web-skill/toggle", data={"enable": "0", "_csrf": token},
                  follow_redirects=False)
    assert resp.status_code == 303
    assert "web-skill" in svc.disabled_names()
    assert "已禁用" in c.get("/skills").text

    # 删除：确认词不匹配 → 400
    resp = c.post("/skills/web-skill/delete", data={"confirm": "x", "_csrf": token})
    assert resp.status_code == 400
    resp = c.post("/skills/web-skill/delete", data={"confirm": "web-skill", "_csrf": token},
                  follow_redirects=False)
    assert resp.status_code == 303
    assert not (hermes_home.home / "skills" / "web-skill").exists()


def test_web_skills_catalog_install_and_settings(logged_in, hermes_home):
    _seed_catalog(hermes_home.home, "research/deep-research", "Deep research")
    c = logged_in
    token = csrf_of(c)

    page = c.get("/skills").text
    assert "官方热门技能库" in page and "deep-research" in page

    resp = c.post("/skills/catalog/install", data={"rel": "research/deep-research",
                                                   "_csrf": token},
                  follow_redirects=False)
    assert resp.status_code == 303
    assert (hermes_home.home / "skills" / "deep-research" / "SKILL.md").exists()

    resp = c.post("/skills/settings", data={
        "external_dirs_text": "~/.agents/skills\n/shared/x",
        "inline_shell": "1", "inline_shell_timeout": "20", "_csrf": token,
    }, follow_redirects=False)
    assert resp.status_code == 303
    s = svc.get_settings()
    assert s.external_dirs == ["~/.agents/skills", "/shared/x"]
    assert s.inline_shell and s.inline_shell_timeout == 20


def test_web_skills_requires_login(client):
    resp = client.get("/skills", follow_redirects=False)
    assert resp.status_code == 302
