"""工程规范层测试：SOUL.md 幂等托管、技能安装、工作区初始化、Web 流程。"""
from __future__ import annotations

from app.hermes import engineering_service as eng


def _soul(paths) -> str:
    return (paths.home / "SOUL.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# SOUL.md 托管块
# ---------------------------------------------------------------------------

def test_compose_block_contains_markers_and_workspace(hermes_home):
    s = eng.EngSettings(workspace="~/ws-custom")
    block = eng.compose_block(s)
    assert eng.SOUL_BEGIN in block and eng.SOUL_END in block
    assert "~/ws-custom" in block
    assert "downloads/" in block and "archive/" in block


def test_apply_writes_soul_and_preserves_user_content(hermes_home):
    soul = hermes_home.home / "SOUL.md"
    soul.parent.mkdir(parents=True, exist_ok=True)
    soul.write_text("# 我的角色\n你是一个严谨的运维助手。\n", encoding="utf-8")
    changes = eng.apply(eng.EngSettings(), hermes_home)
    assert any("SOUL.md" in c for c in changes)
    text = _soul(hermes_home)
    assert "我的角色" in text                       # 用户内容保留
    assert eng.SOUL_BEGIN in text and eng.SOUL_END in text
    assert text.index("我的角色") < text.index(eng.SOUL_BEGIN)


def test_apply_is_idempotent(hermes_home):
    s = eng.EngSettings()
    eng.apply(s, hermes_home)
    first = _soul(hermes_home)
    changes = eng.apply(s, hermes_home)
    assert _soul(hermes_home) == first
    assert any("无变更" in c for c in changes)
    assert first.count(eng.SOUL_BEGIN) == 1


def test_re_apply_updates_block_without_duplicating(hermes_home):
    eng.apply(eng.EngSettings(workspace="~/ws-a"), hermes_home)
    eng.apply(eng.EngSettings(workspace="~/ws-b"), hermes_home)
    text = _soul(hermes_home)
    assert text.count(eng.SOUL_BEGIN) == 1
    assert "~/ws-b" in text and "~/ws-a" not in text


def test_remove_strips_block_keeps_user_content(hermes_home):
    soul = hermes_home.home / "SOUL.md"
    soul.parent.mkdir(parents=True, exist_ok=True)
    soul.write_text("# 保留我\n", encoding="utf-8")
    eng.apply(eng.EngSettings(), hermes_home)
    changes = eng.remove(paths=hermes_home)
    text = _soul(hermes_home)
    assert "保留我" in text
    assert eng.SOUL_BEGIN not in text
    assert any("移除" in c for c in changes)


# ---------------------------------------------------------------------------
# 技能
# ---------------------------------------------------------------------------

def test_install_and_remove_skills(hermes_home):
    eng.apply(eng.EngSettings(), hermes_home)
    st = eng.skills_status(hermes_home)
    assert all(st.values()), st
    doc = (eng.skills_dir(hermes_home) / "project-init" / "SKILL.md").read_text(encoding="utf-8")
    assert doc.startswith("---\nname: project-init")
    assert "managed-by: hermes-console" in doc
    assert "## When to Use" in doc

    removed = eng.remove_skills(hermes_home)
    assert len(removed) == 3
    assert not any(eng.skills_status(hermes_home).values())


def test_remove_does_not_touch_foreign_skills(hermes_home):
    foreign = eng.skills_dir(hermes_home) / "someone-elses"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("---\nname: someone-elses\n---\n", encoding="utf-8")
    eng.apply(eng.EngSettings(), hermes_home)
    eng.remove(paths=hermes_home)
    assert foreign.exists()


# ---------------------------------------------------------------------------
# coding_instructions
# ---------------------------------------------------------------------------

def test_coding_instructions_toggle(hermes_home):
    eng.apply(eng.EngSettings(inject_coding_instructions=True), hermes_home)
    assert get_ci(hermes_home) is not None
    eng.apply(eng.EngSettings(inject_coding_instructions=False), hermes_home)
    assert get_ci(hermes_home) is None


def get_ci(paths):
    from app.hermes.config_store import get_path, load_config

    return (get_path(load_config(paths), "agent", {}) or {}).get("coding_instructions")


# ---------------------------------------------------------------------------
# 工作区初始化
# ---------------------------------------------------------------------------

def test_workspace_init_creates_tree(hermes_home, tmp_path, monkeypatch):
    base = tmp_path / "ws"
    s = eng.EngSettings(workspace=str(base))
    created = eng.init_workspace(s, hermes_home)
    assert (base / "projects").is_dir()
    assert (base / "downloads").is_dir()
    assert (base / "scratch").is_dir()
    assert (base / "archive").is_dir()
    assert (base / "README.md").exists()
    assert (base / "projects" / "example-project" / "docs" / "requirements.md").exists()
    assert "## 验收标准" in (base / "projects" / "example-project" /
                             "docs" / "requirements.md").read_text(encoding="utf-8")
    # 幂等：二次初始化不新建
    assert eng.init_workspace(s, hermes_home) == []


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------

def test_status_reflects_states(hermes_home):
    st = eng.get_status(hermes_home)
    assert st.soul_managed is False
    assert set(st.skills) == {"project-init", "requirement-digest", "file-placement"}
    eng.apply(eng.EngSettings(), hermes_home)
    st = eng.get_status(hermes_home)
    assert st.soul_managed is True
    assert all(st.skills.values())


# ---------------------------------------------------------------------------
# Web 流程
# ---------------------------------------------------------------------------

def test_engineering_page_and_flows(logged_in):
    from app.hermes.paths import detect

    from tests.conftest import csrf_of

    c = logged_in
    page = c.get("/engineering")
    assert page.status_code == 200
    assert "工程规范" in page.text

    token = csrf_of(c)
    resp = c.post("/engineering/apply", data={
        "workspace": "~/ws-test", "rules": "第一条铁律\n第二条铁律",
        "inject_coding_instructions": "on",
        "skills": ["project-init", "file-placement"], "_csrf": token},
        follow_redirects=False)
    assert resp.status_code == 303

    assert "工程规范：已启用" in c.get("/engineering").text
    paths = detect()
    text = _soul(paths)
    assert "~/ws-test" in text and "第一条铁律" in text
    assert eng.skills_status(paths)["project-init"] is True

    resp = c.post("/engineering/remove", data={"keep_skills": "off", "_csrf": token},
                  follow_redirects=False)
    assert resp.status_code == 303
    assert eng.SOUL_BEGIN not in _soul(detect())
