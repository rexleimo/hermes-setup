"""插件管理：~/.hermes/plugins/ 白名单模型 + 官方策展目录 + 官方 CLI 安装。

对齐 Hermes 官方语义（website/docs/user-guide/features/plugins.md）：
- 插件目录 = ~/.hermes/plugins/<name>/（plugin.yaml + __init__.py 暴露 register(ctx)）；
- **默认禁用**：必须显式列入 config.yaml `plugins.enabled` 白名单才会加载
  （防第三方代码未经同意执行），本模块的启停即增删白名单项；
- 安装/更新/卸载走官方 CLI（hermes plugins install，含 sha pin 与黑名单校验），
  通过后台任务执行（复用 installer 的 job_runs 台账），不自研 git clone；
- 官方策展目录 = agent_repo/plugin-catalog/*.yaml（removed.yaml 为黑名单）。
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML

from app.core import db
from app.hermes import installer
from app.hermes.config_store import ensure_path, get_path, load_config, save_config
from app.hermes.paths import HermesPaths, detect, resolve_agent_repo

_yaml = YAML(typ="safe", pure=True)


class PluginError(Exception):
    pass


# ---------------------------------------------------------------------------
# 已安装插件
# ---------------------------------------------------------------------------

@dataclass
class PluginView:
    name: str
    version: str = ""
    description: str = ""
    author: str = ""
    hooks: list[str] = field(default_factory=list)
    provides_hooks: list[str] = field(default_factory=list)
    enabled: bool = False            # 是否在 plugins.enabled 白名单
    broken: bool = False             # 白名单里有但目录缺失（残留脏键）
    dir: str = ""                    # 相对 plugins/ 的目录（嵌套为 "memory/agentmemory"）
    auto_loaded: bool = False        # 官方豁免类目（memory/platforms/…）：不依赖白名单自动加载

    @property
    def state_label(self) -> str:
        if self.broken:
            return "目录缺失"
        if self.auto_loaded:
            return "自动加载"
        return "已启用" if self.enabled else "已禁用"


# 嵌套子目录的官方豁免类目：走专用加载器自动加载，不经 plugins.enabled 白名单
# （官方 plugins 文档：bundled infrastructure — platform adapters / image-gen /
#   memory providers / context engines / model providers）。
EXEMPT_SUBDIRS = ("platforms", "image_gen", "memory", "context_engine", "model-providers")


def plugins_dir(paths: HermesPaths | None = None) -> Path:
    paths = paths or detect()
    return paths.home / "plugins"


def enabled_names(paths: HermesPaths | None = None) -> list[str]:
    raw = get_path(load_config(paths), "plugins.enabled", [])
    return [str(x) for x in raw] if isinstance(raw, list) else []


def list_plugins(paths: HermesPaths | None = None) -> list[PluginView]:
    paths = paths or detect()
    base = plugins_dir(paths)
    whitelist = enabled_names(paths)
    seen: set[str] = set()
    out: list[PluginView] = []
    # 递归两层：官方嵌套类目（plugins/memory/<name> 等）也是合法插件目录，
    # 只扫一层的话 agentmemory 装完在页面上永远看不见（实机踩过）。
    for pattern in ("*/plugin.yaml", "*/*/plugin.yaml"):
        for plugin_yaml in sorted(base.glob(pattern)):
            try:
                data = _yaml.load(plugin_yaml.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            name = str(data.get("name") or plugin_yaml.parent.name)
            rel_dir = plugin_yaml.parent.relative_to(base).as_posix()
            auto = "/" in rel_dir and rel_dir.split("/", 1)[0] in EXEMPT_SUBDIRS
            if name in seen:
                continue
            seen.add(name)
            out.append(PluginView(
                name=name,
                version=str(data.get("version") or ""),
                description=str(data.get("description") or "").strip(),
                author=str(data.get("author") or ""),
                hooks=[str(h) for h in data.get("hooks", [])]
                if isinstance(data.get("hooks"), list) else [],
                provides_hooks=[str(h) for h in data.get("provides_hooks", [])]
                if isinstance(data.get("provides_hooks"), list) else [],
                enabled=name in whitelist or auto,
                broken=False,
                dir=rel_dir,
                auto_loaded=auto,
            ))
    # 白名单残留：目录已删但名单没清
    for name in whitelist:
        if name not in seen:
            out.append(PluginView(name=name, broken=True, enabled=True))
    out.sort(key=lambda p: (not p.enabled, p.name))
    return out


def get_plugin(name: str, paths: HermesPaths | None = None) -> PluginView:
    for p in list_plugins(paths):
        if p.name == name:
            return p
    raise PluginError(f"插件不存在：{name}")


def set_enabled(name: str, enabled: bool, paths: HermesPaths | None = None) -> None:
    """启停 = 增删 plugins.enabled 白名单（官方信任模型：不在名单 = 不加载）。"""
    paths = paths or detect()
    if not _dir_exists(name, paths) and enabled:
        raise PluginError(f"插件目录不存在：{name}")
    config = load_config(paths)
    node = ensure_path(config, "plugins")
    whitelist = [str(x) for x in node.get("enabled", [])] \
        if isinstance(node.get("enabled"), list) else []
    if enabled and name not in whitelist:
        whitelist.append(name)
    elif not enabled:
        whitelist = [x for x in whitelist if x != name]
    if whitelist:
        node["enabled"] = whitelist
    else:
        node.pop("enabled", None)
        if not node:
            config.pop("plugins", None)
    save_config(config, paths)


def remove_plugin(name: str, paths: HermesPaths | None = None) -> list[str]:
    paths = paths or detect()
    target = None
    for p in list_plugins(paths):
        if p.name == name and not p.broken:
            target = plugins_dir(paths) / p.dir
            break
    if target is None or not target.is_dir():
        raise PluginError(f"插件目录不存在：{name}")
    shutil.rmtree(target)
    notes = [f"已删除插件目录 {name}"]
    if name in enabled_names(paths):
        set_enabled(name, False, paths)
        notes.append("已从 plugins.enabled 白名单移除")
    return notes


def _dir_exists(name: str, paths: HermesPaths) -> bool:
    return (plugins_dir(paths) / name).is_dir()


def get_hook_timeout(paths: HermesPaths | None = None) -> int:
    raw = get_path(load_config(paths), "plugins.hook_callback_timeout", 30)
    return int(raw) if isinstance(raw, (int, float)) else 30


def set_hook_timeout(seconds: int, paths: HermesPaths | None = None) -> None:
    if not 1 <= seconds <= 600:
        raise PluginError("插件 hook 回调超时需在 1-600 秒之间（官方默认 30）")
    paths = paths or detect()
    config = load_config(paths)
    node = ensure_path(config, "plugins")
    if seconds == 30:
        node.pop("hook_callback_timeout", None)  # 默认值不落键
    else:
        node["hook_callback_timeout"] = seconds
    if not node:
        config.pop("plugins", None)
    save_config(config, paths)


# ---------------------------------------------------------------------------
# 官方策展目录（agent_repo/plugin-catalog/*.yaml）
# ---------------------------------------------------------------------------

@dataclass
class PluginCatalogEntry:
    name: str
    repo: str = ""
    sha: str = ""
    description: str = ""
    tier: str = ""
    docs_url: str = ""
    provides_tools: int = 0
    provides_hooks: int = 0
    requires_env: list[str] = field(default_factory=list)


def catalog(paths: HermesPaths | None = None) -> list[PluginCatalogEntry]:
    paths = paths or detect()
    base = (resolve_agent_repo(paths) or paths.agent_repo) / "plugin-catalog"
    if not base.is_dir():
        return []
    out: list[PluginCatalogEntry] = []
    for catalog_yaml in sorted(base.glob("*.yaml")):
        if catalog_yaml.name == "removed.yaml":
            continue
        try:
            data = _yaml.load(catalog_yaml.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or not data.get("name"):
            continue
        caps = data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {}
        tools = caps.get("provides_tools")
        hooks = caps.get("provides_hooks")
        env = caps.get("requires_env")
        out.append(PluginCatalogEntry(
            name=str(data["name"]),
            repo=str(data.get("repo") or ""),
            sha=str(data.get("sha") or ""),
            description=str(data.get("description") or "").strip(),
            tier=str(data.get("tier") or ""),
            docs_url=str(data.get("docs_url") or ""),
            provides_tools=len(tools) if isinstance(tools, list) else 0,
            provides_hooks=len(hooks) if isinstance(hooks, list) else 0,
            requires_env=[str(e) for e in env] if isinstance(env, list) else [],
        ))
    return out


def install(name: str, paths: HermesPaths | None = None) -> int:
    """提交官方 CLI 安装任务（sha pin / 黑名单 / 信任校验全部交给官方实现）。

    --enable：后台任务无 TTY，官方 CLI 装完的 "Enable now? [y/N]" 永远取默认 No，
    不加它就是"装完即隐身"。命令按平台拼引号：shlex.quote 产出 POSIX 单引号，
    Windows 的 cmd.exe 不认（反斜杠路径必被包进 '...'，命令必败，实机踩过）；
    Windows 走 subprocess.list2cmdline 的双引号规则。"""
    name = name.strip()
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$", name):
        raise PluginError("插件名不合法")
    paths = paths or detect()
    if not paths.bin:
        raise PluginError("未找到 hermes 命令，请先在「服务管理」安装 Hermes Agent")
    if _dir_exists(name, paths):
        raise PluginError(f"插件已安装：{name}（更新请用服务管理的 hermes update）")
    argv = [paths.bin, "plugins", "install", name, "--enable"]
    command = subprocess.list2cmdline(argv) if sys.platform == "win32" else shlex.join(argv)
    try:
        return installer.submit("plugin_install", command, shell=True)
    except installer.JobBusy:
        raise PluginError("已有后台任务在执行中，请等待完成")
