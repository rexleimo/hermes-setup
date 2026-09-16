"""Skill 管理：~/.hermes/skills/ 可视化、官方热门技能库、skills 配置域。

对齐 Hermes 官方语义（hermes-agent/agent/skill_utils.py 与
hermes_cli/config_defaults.py 的 skills 域）：
- 技能目录 = ~/.hermes/skills/<slug>/SKILL.md（agentskills.io 兼容 frontmatter）；
- 启停 = config.yaml `skills.disabled` 名单；`hermes-agent` 为官方必备技能，
  官方加载器会把它从禁用名单中剔除（ESSENTIAL_SKILLS），本模块同样拒绝禁用；
- 安装 = 把技能目录复制进 skills/（与官方 `hermes skills install` 行为一致）；
- 工程规范托管的技能（SKILL_MARKER）只能从「工程规范」页卸载。
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML

from app.hermes import engineering_service
from app.hermes.config_store import ensure_path, get_path, load_config, save_config
from app.hermes.paths import HermesPaths, detect

SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")
ESSENTIAL_SKILLS = frozenset({"hermes-agent"})
_yaml = YAML(typ="safe", pure=True)


class SkillError(Exception):
    pass


# ---------------------------------------------------------------------------
# 视图模型
# ---------------------------------------------------------------------------

@dataclass
class SkillView:
    slug: str
    name: str = ""
    description: str = ""
    version: str = ""
    tags: list[str] = field(default_factory=list)
    origin: str = "user"        # engineering | catalog | user
    disabled: bool = False
    broken: bool = False        # 目录存在但缺 SKILL.md

    @property
    def display_name(self) -> str:
        return self.name or self.slug

    @property
    def origin_label(self) -> str:
        return {"engineering": "工程规范托管", "catalog": "官方目录", "user": "自定义"}[self.origin]


@dataclass
class CatalogSkill:
    slug: str
    rel: str                    # 相对 optional-skills 的路径（安装时用）
    category: str
    name: str = ""
    description: str = ""
    version: str = ""
    tags: list[str] = field(default_factory=list)
    installed: bool = False


# ---------------------------------------------------------------------------
# frontmatter 解析
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> dict:
    """解析 SKILL.md 头部 YAML frontmatter；不合法返回 {}。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    try:
        data = _yaml.load(text[3:end])
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _hermes_meta(fm: dict) -> dict:
    meta = fm.get("metadata")
    if isinstance(meta, dict) and isinstance(meta.get("hermes"), dict):
        return meta["hermes"]
    return {}


def _skill_view(slug: str, base: Path, disabled: set[str],
                paths: HermesPaths) -> SkillView:
    doc = base / slug / "SKILL.md"
    if not doc.exists():
        return SkillView(slug=slug, broken=True)
    fm = _parse_frontmatter(doc.read_text(encoding="utf-8", errors="replace"))
    meta = _hermes_meta(fm)
    origin = "user"
    if is_console_managed(doc):
        origin = "engineering"
    elif _in_official_catalog(slug, paths):
        origin = "catalog"
    tags = meta.get("tags")
    return SkillView(
        slug=slug,
        name=str(fm.get("name") or ""),
        description=str(fm.get("description") or "").strip(),
        version=str(fm.get("version") or ""),
        tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        origin=origin,
        disabled=slug in disabled,
    )


def is_console_managed(doc: Path) -> bool:
    return engineering_service.SKILL_MARKER in doc.read_text(encoding="utf-8", errors="replace")


def _in_official_catalog(slug: str, paths: HermesPaths) -> bool:
    base = paths.agent_repo / "optional-skills"
    return base.is_dir() and any(p.parent.name == slug for p in base.glob(f"*/{slug}/SKILL.md"))


# ---------------------------------------------------------------------------
# 已安装技能
# ---------------------------------------------------------------------------

def installed_skills(paths: HermesPaths | None = None) -> list[SkillView]:
    paths = paths or detect()
    base = engineering_service.skills_dir(paths)
    disabled = set(disabled_names(paths))
    if not base.is_dir():
        return []
    views = [_skill_view(p.name, base, disabled, paths)
             for p in sorted(base.iterdir()) if p.is_dir()]
    views.sort(key=lambda v: (v.disabled, v.origin != "engineering", v.slug))
    return views


def disabled_names(paths: HermesPaths | None = None) -> list[str]:
    config = load_config(paths)
    raw = get_path(config, "skills.disabled", [])
    if isinstance(raw, str):
        # 容错：用户手写成 JSON 字符串（本机实例出现过 '["a"]' 的写法）
        import json

        try:
            raw = json.loads(raw)
        except ValueError:
            raw = [raw]
    return [str(x) for x in raw] if isinstance(raw, list) else []


def toggle_skill(slug: str, enabled: bool, paths: HermesPaths | None = None) -> None:
    if slug in ESSENTIAL_SKILLS and not enabled:
        raise SkillError(f"{slug} 是官方必备技能，不能禁用")
    paths = paths or detect()
    config = load_config(paths)
    disabled = disabled_names(paths)
    if enabled:
        disabled = [s for s in disabled if s != slug]
    elif slug not in disabled:
        disabled.append(slug)
    _write_disabled(disabled, config, paths)


def remove_skill(slug: str, paths: HermesPaths | None = None) -> None:
    paths = paths or detect()
    doc = engineering_service.skills_dir(paths) / slug / "SKILL.md"
    if slug in ESSENTIAL_SKILLS:
        raise SkillError(f"{slug} 是官方必备技能，不能删除")
    if doc.exists() and is_console_managed(doc):
        raise SkillError("该技能由「工程规范」模块托管，请到工程规范页卸载")
    base = engineering_service.skills_dir(paths)
    target = base / slug
    if not target.is_dir():
        raise SkillError(f"技能目录不存在：{slug}")
    shutil.rmtree(target)
    # 顺带从禁用名单摘除，避免残留脏键
    if slug in disabled_names(paths):
        config = load_config(paths)
        _write_disabled([s for s in disabled_names(paths) if s != slug], config, paths)


def _write_disabled(names: list[str], config, paths: HermesPaths) -> None:
    node = ensure_path(config, "skills")
    if names:
        node["disabled"] = sorted(names)
    else:
        node.pop("disabled", None)
        if not node:
            config.pop("skills", None)
    save_config(config, paths)


# ---------------------------------------------------------------------------
# 官方热门技能库（agent_repo/optional-skills/<类目>/<技能>/SKILL.md）
# ---------------------------------------------------------------------------

def catalog_base(paths: HermesPaths | None = None) -> Path:
    paths = paths or detect()
    return paths.agent_repo / "optional-skills"


def catalog(paths: HermesPaths | None = None) -> list[CatalogSkill]:
    base = catalog_base(paths)
    if not base.is_dir():
        return []
    installed_slugs = {v.slug for v in installed_skills(paths)}
    out: list[CatalogSkill] = []
    for doc in sorted(base.glob("*/*/SKILL.md")):
        slug = doc.parent.name
        fm = _parse_frontmatter(doc.read_text(encoding="utf-8", errors="replace"))
        meta = _hermes_meta(fm)
        tags = meta.get("tags")
        out.append(CatalogSkill(
            slug=slug,
            rel=str(doc.parent.relative_to(base)),
            category=doc.parent.parent.name,
            name=str(fm.get("name") or ""),
            description=str(fm.get("description") or "").strip(),
            version=str(fm.get("version") or ""),
            tags=[str(t) for t in tags] if isinstance(tags, list) else [],
            installed=slug in installed_slugs,
        ))
    out.sort(key=lambda s: (s.installed, s.category, s.slug))
    return out


def install_from_catalog(rel: str, paths: HermesPaths | None = None) -> str:
    """把官方目录技能复制进 ~/.hermes/skills/（与 `hermes skills install` 等效）。"""
    paths = paths or detect()
    base = catalog_base(paths)
    if not base.is_dir():
        raise SkillError("官方技能库不可用：请先在「服务管理」安装 Hermes Agent")
    source = (base / rel).resolve()
    if base.resolve() not in source.parents or source.name in ("", "."):
        raise SkillError("非法的技能路径")
    if not (source / "SKILL.md").exists():
        raise SkillError(f"目录中缺少 SKILL.md：{rel}")
    slug = source.name
    if not SLUG_RE.match(slug):
        raise SkillError(f"技能目录名不合法：{slug}")
    target = engineering_service.skills_dir(paths) / slug
    if target.exists():
        raise SkillError(f"已存在同名技能：{slug}（如需重装请先删除）")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    return slug


# ---------------------------------------------------------------------------
# skills 配置域（external_dirs / 信任目录 / 安全开关）
# ---------------------------------------------------------------------------

@dataclass
class SkillsSettings:
    external_dirs: list[str] = field(default_factory=list)
    trusted_project_dirs: list[str] = field(default_factory=list)
    project_discovery: bool = True
    template_vars: bool = True
    inline_shell: bool = False
    inline_shell_timeout: int = 10
    guard_agent_created: bool = False


def get_settings(paths: HermesPaths | None = None) -> SkillsSettings:
    config = load_config(paths)
    node = get_path(config, "skills", {})
    ext = node.get("external_dirs", []) if isinstance(node, dict) else []
    if isinstance(ext, str):
        import json

        try:
            ext = json.loads(ext)
        except ValueError:
            ext = [ext]
    trusted = node.get("trusted_project_dirs", []) if isinstance(node, dict) else []
    timeout = node.get("inline_shell_timeout", 10) if isinstance(node, dict) else 10
    return SkillsSettings(
        external_dirs=[str(x) for x in ext] if isinstance(ext, list) else [],
        trusted_project_dirs=[str(x) for x in trusted] if isinstance(trusted, list) else [],
        project_discovery=bool(node.get("project_discovery", True)) if isinstance(node, dict) else True,
        template_vars=bool(node.get("template_vars", True)) if isinstance(node, dict) else True,
        inline_shell=bool(node.get("inline_shell", False)) if isinstance(node, dict) else False,
        inline_shell_timeout=int(timeout) if isinstance(timeout, (int, float)) else 10,
        guard_agent_created=bool(node.get("guard_agent_created", False)) if isinstance(node, dict) else False,
    )


def save_settings(s: SkillsSettings, paths: HermesPaths | None = None) -> None:
    if s.inline_shell_timeout < 1 or s.inline_shell_timeout > 300:
        raise SkillError("inline_shell 超时需在 1-300 秒之间")
    paths = paths or detect()
    config = load_config(paths)
    node = ensure_path(config, "skills")
    if s.external_dirs:
        node["external_dirs"] = s.external_dirs
    else:
        node.pop("external_dirs", None)
    if s.project_discovery is not True:
        node["project_discovery"] = s.project_discovery
    if s.template_vars is not True:
        node["template_vars"] = s.template_vars
    node["inline_shell"] = s.inline_shell
    if s.inline_shell:
        node["inline_shell_timeout"] = s.inline_shell_timeout
    else:
        node.pop("inline_shell_timeout", None)
    if s.guard_agent_created is not False:
        node["guard_agent_created"] = s.guard_agent_created
    else:
        node.pop("guard_agent_created", None)
    if not node:
        config.pop("skills", None)
    save_config(config, paths)


def untrust_project_dir(path: str, paths: HermesPaths | None = None) -> None:
    """等效 `hermes skills untrust <dir>`：从 trusted_project_dirs 摘除。"""
    paths = paths or detect()
    config = load_config(paths)
    node = get_path(config, "skills", None)
    trusted = node.get("trusted_project_dirs") if isinstance(node, dict) else None
    if not isinstance(trusted, list) or path not in [str(x) for x in trusted]:
        raise SkillError(f"信任目录不存在：{path}")
    node["trusted_project_dirs"] = [x for x in trusted if str(x) != path]
    save_config(config, paths)
