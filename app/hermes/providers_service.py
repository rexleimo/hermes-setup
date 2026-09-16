"""模型供应商管理：平台元数据（DB）+ Hermes 原生配置（config.yaml / .env）。

写入规则（与 Hermes 官方 schema 对齐，见 app/hermes/schema.py）：
- custom 供应商 → config.yaml `providers.<id>: {base_url, api_mode, key_env}`，
  API Key 只写 ~/.hermes/.env（config 中仅存键名，敏感值不进 config.yaml）；
- preset 供应商 → 仅写 .env 的官方约定键（如 OPENROUTER_API_KEY）；
- 设为主模型 → `model.{provider, default, base_url, api_mode, context_length}`；
- 模型别名 → `model_aliases.<alias>: {model, provider, base_url?}`；
- 备选链 → `delegation.fallback_providers: [{provider, model, ...}]`。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ruamel.yaml.comments import CommentedMap

from app.core import db
from app.hermes import model_catalog
from app.hermes.config_store import (
    ConfigError, EnvStore, ensure_path, get_path, load_config, save_config,
)
from app.hermes.paths import detect
from app.hermes.schema import PRESETS, PROTOCOLS

ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


class ProviderError(Exception):
    pass


# ---------------------------------------------------------------------------
# 视图模型
# ---------------------------------------------------------------------------

@dataclass
class ProviderView:
    id: str
    name: str
    kind: str                      # preset | custom
    preset_id: str | None
    protocol: str
    base_url: str
    env_key: str
    default_model: str
    note: str = ""
    has_api_key: bool = False
    model_count: int = 0
    is_main: bool = False
    main_model: str = ""

    @property
    def protocol_label(self) -> str:
        return PROTOCOLS[self.protocol].label if self.protocol in PROTOCOLS else self.protocol

    @property
    def preset(self) -> object | None:
        return PRESETS.get(self.preset_id) if self.preset_id else None


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def list_providers() -> list[ProviderView]:
    config = load_config()
    env = EnvStore.load()
    main_provider = str(get_path(config, "model.provider", "") or "")
    main_model = str(get_path(config, "model.default", "") or "")

    metas = db.query("SELECT * FROM provider_meta ORDER BY created_at")
    views: list[ProviderView] = []
    for m in metas:
        pid = m["id"]
        views.append(ProviderView(
            id=pid, name=m["name"], kind=m["kind"], preset_id=m["preset_id"],
            protocol=m["protocol"], base_url=m["base_url"], env_key=m["env_key"],
            default_model=m["default_model"], note=m["note"],
            has_api_key=bool(env.get(m["env_key"])),
            model_count=db.query_one(
                "SELECT COUNT(*) AS n FROM provider_models WHERE provider_id = ?", (pid,))["n"],
            is_main=main_provider == _hermes_provider_id(m) and bool(main_model),
            main_model=main_model if main_provider == _hermes_provider_id(m) else "",
        ))
    return views


def get_provider(pid: str) -> ProviderView:
    row = db.query_one("SELECT * FROM provider_meta WHERE id = ?", (pid,))
    if row is None:
        raise ProviderError(f"供应商不存在：{pid}")
    views = list_providers()
    return next(v for v in views if v.id == pid)


def _hermes_provider_id(meta) -> str:
    """该供应商在 model.provider 中的取值：preset 用预设 id，custom 用记录 id。"""
    return meta["preset_id"] if meta["kind"] == "preset" else meta["id"]


def list_models(pid: str) -> list:
    return db.query(
        "SELECT * FROM provider_models WHERE provider_id = ? ORDER BY model_id", (pid,))


def api_key_for(pid: str) -> str | None:
    meta = db.query_one("SELECT env_key FROM provider_meta WHERE id = ?", (pid,))
    if meta is None:
        return None
    return EnvStore.load().get(meta["env_key"])


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def _check_protocol_env_key(protocol: str, env_key: str) -> str:
    proto = PROTOCOLS[protocol]
    if proto.env_key_hint and proto.env_key_hint != "auto" and env_key == "auto":
        return proto.env_key_hint.split(" / ")[0]
    return env_key


def create_provider(*, pid: str, name: str, kind: str, preset_id: str | None,
                    protocol: str, base_url: str, env_key: str,
                    api_key: str, default_model: str, note: str) -> None:
    if kind == "preset":
        if preset_id not in PRESETS:
            raise ProviderError("预设不存在")
        preset = PRESETS[preset_id]
        protocol = preset.protocol
        base_url = preset.base_url
        env_key = preset.env_key
        pid = preset_id  # 预设供应商直接以预设 id 为主键
        if db.query_one("SELECT 1 FROM provider_meta WHERE id = ?", (pid,)):
            raise ProviderError("该预设供应商已添加")
    else:
        pid = pid.strip()
        if not ID_RE.match(pid):
            raise ProviderError("标识需为 2-32 位小写字母开头的 [a-z0-9_-]")
        if db.query_one("SELECT 1 FROM provider_meta WHERE id = ?", (pid,)):
            raise ProviderError(f"标识已存在：{pid}")
        if protocol not in PROTOCOLS:
            raise ProviderError("协议不合法")
        env_key = env_key.strip() or f"HERMES_PROVIDER_{pid.upper().replace('-', '_')}_API_KEY"
        env_key = _check_protocol_env_key(protocol, env_key)

    db.execute(
        "INSERT INTO provider_meta (id, name, kind, preset_id, protocol, env_key, base_url,"
        " default_model, note) VALUES (?,?,?,?,?,?,?,?,?)",
        (pid,
         name.strip() or (PRESETS[preset_id].label if kind == "preset" and preset_id else pid),
         kind, preset_id, protocol, env_key, base_url.strip(),
         default_model.strip(), note.strip()),
    )
    if api_key.strip():
        set_api_key(pid, api_key.strip())
    _write_custom_section(pid)


def _meta(pid: str):
    row = db.query_one("SELECT * FROM provider_meta WHERE id = ?", (pid,))
    if row is None:
        raise ProviderError(f"供应商不存在：{pid}")
    return row


def _write_custom_section(pid: str) -> None:
    """把 custom 供应商写入 config.yaml providers.<id>（幂等）。"""
    meta = _meta(pid)
    if meta["kind"] != "custom":
        return
    proto = PROTOCOLS[meta["protocol"]]
    config = load_config()
    node = ensure_path(config, f"providers.{pid}")
    node["base_url"] = meta["base_url"]
    if proto.api_mode:
        node["api_mode"] = proto.api_mode
    node["key_env"] = meta["env_key"]
    save_config(config)


def update_provider(pid: str, *, name: str, base_url: str, env_key: str,
                    default_model: str, note: str) -> None:
    meta = _meta(pid)
    if meta["kind"] == "preset":
        base_url, env_key = meta["base_url"], meta["env_key"]
    db.execute(
        "UPDATE provider_meta SET name = ?, base_url = ?, env_key = ?, default_model = ?,"
        " note = ?, updated_at = datetime('now') WHERE id = ?",
        (name.strip() or pid, base_url.strip(), env_key.strip(), default_model.strip(),
         note.strip(), pid),
    )
    _write_custom_section(pid)


def delete_provider(pid: str) -> list[str]:
    """删除供应商；返回清理说明。顺带从主模型/备选链中摘除引用。"""
    meta = _meta(pid)
    notes: list[str] = []
    config = load_config()

    hermes_pid = _hermes_provider_id(meta)
    if str(get_path(config, "model.provider", "")) == hermes_pid:
        model_node = ensure_path(config, "model")
        model_node.pop("provider", None)
        model_node.pop("default", None)
        notes.append("已从主模型（model.*）中摘除该供应商，需重新指定主模型")

    fb = get_fallback(config)
    if any(e.provider == hermes_pid for e in fb):
        set_fallback([e for e in fb if e.provider != hermes_pid], config=config)
        notes.append("已从备选链（delegation.fallback_providers）中移除相关条目")

    providers_node = get_path(config, "providers", None)
    if isinstance(providers_node, dict) and pid in providers_node:
        providers_node.pop(pid)
        notes.append("已移除 config.yaml providers 节")

    save_config(config)

    env = EnvStore.load()
    if env.get(meta["env_key"]) is not None:
        env.set(meta["env_key"], "")
        env.save()
        notes.append(f"已从 .env 删除键 {meta['env_key']}")

    db.execute("DELETE FROM provider_meta WHERE id = ?", (pid,))
    db.execute("DELETE FROM provider_models WHERE provider_id = ?", (pid,))
    return notes


def set_api_key(pid: str, api_key: str) -> None:
    meta = _meta(pid)
    env = EnvStore.load()
    env.set(meta["env_key"], api_key)
    env.save()
    env.chmod_600()


def set_main_model(pid: str, model_id: str, context_length: int | None = None) -> None:
    meta = _meta(pid)
    if not model_id.strip():
        raise ProviderError("模型 ID 不能为空")
    config = load_config()
    model_node = ensure_path(config, "model")
    model_node["provider"] = _hermes_provider_id(meta)
    model_node["default"] = model_id.strip()
    if meta["kind"] == "custom":
        if meta["base_url"]:
            model_node["base_url"] = meta["base_url"]
        proto = PROTOCOLS[meta["protocol"]]
        if proto.api_mode:
            model_node["api_mode"] = proto.api_mode
        else:
            model_node.pop("api_mode", None)
    else:
        model_node.pop("base_url", None)
        model_node.pop("api_mode", None)
    if context_length and context_length > 0:
        model_node["context_length"] = int(context_length)
    else:
        model_node.pop("context_length", None)
    save_config(config)
    db.execute(
        "UPDATE provider_meta SET default_model = ?, updated_at = datetime('now')"
        " WHERE id = ?", (model_id.strip(), pid))


# ---------------------------------------------------------------------------
# 模型目录（平台侧元数据）
# ---------------------------------------------------------------------------

def upsert_model(pid: str, model_id: str, display_name: str = "",
                 context_length: int | None = None, note: str = "") -> None:
    db.execute(
        "INSERT INTO provider_models (provider_id, model_id, display_name, context_length, note)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT(provider_id, model_id) DO UPDATE SET"
        " display_name = excluded.display_name, context_length = excluded.context_length,"
        " note = excluded.note, updated_at = datetime('now')",
        (pid, model_id, display_name, context_length, note),
    )


def remove_model(pid: str, model_id: str) -> None:
    db.execute("DELETE FROM provider_models WHERE provider_id = ? AND model_id = ?",
               (pid, model_id))


# ---------------------------------------------------------------------------
# 别名与备选链
# ---------------------------------------------------------------------------

def publish_alias(pid: str, model_id: str, alias: str) -> None:
    meta = _meta(pid)
    alias = alias.strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", alias):
        raise ProviderError("别名不合法（字母数字开头，可含 . _ -）")
    config = load_config()
    node = ensure_path(config, "model_aliases")
    entry = CommentedMap()
    entry["model"] = model_id
    entry["provider"] = _hermes_provider_id(meta)
    if meta["kind"] == "custom":
        entry["base_url"] = meta["base_url"]
        entry["key_env"] = meta["env_key"]
    node[alias] = entry
    save_config(config)


def list_aliases() -> dict:
    config = load_config()
    aliases = get_path(config, "model_aliases", {})
    return dict(aliases) if isinstance(aliases, dict) else {}


def remove_alias(alias: str) -> None:
    config = load_config()
    node = get_path(config, "model_aliases", None)
    if isinstance(node, dict) and alias in node:
        node.pop(alias)
        save_config(config)


@dataclass(frozen=True)
class ChainEntry:
    provider: str
    model: str
    base_url: str = ""
    key_env: str = ""


def get_fallback(config: CommentedMap | None = None) -> list[ChainEntry]:
    config = config if config is not None else load_config()
    raw = get_path(config, "delegation.fallback_providers", [])
    out: list[ChainEntry] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("provider") and item.get("model"):
                out.append(ChainEntry(
                    provider=str(item["provider"]), model=str(item["model"]),
                    base_url=str(item.get("base_url") or ""),
                    key_env=str(item.get("key_env") or ""),
                ))
    return out


def set_fallback(entries: list[ChainEntry], config: CommentedMap | None = None) -> None:
    config = config if config is not None else load_config()
    node = ensure_path(config, "delegation")
    if not entries:
        node.pop("fallback_providers", None)
        if not node:
            config.pop("delegation", None)
    else:
        arr = []
        for e in entries:
            item = CommentedMap()
            item["provider"] = e.provider
            item["model"] = e.model
            if e.base_url:
                item["base_url"] = e.base_url
            if e.key_env:
                item["key_env"] = e.key_env
            arr.append(item)
        node["fallback_providers"] = arr
    save_config(config)


# ---------------------------------------------------------------------------
# 连接测试 / 模型拉取
# ---------------------------------------------------------------------------

async def fetch_remote_models(pid: str) -> list[model_catalog.ModelInfo]:
    meta = _meta(pid)
    proto = PROTOCOLS[meta["protocol"]]
    api_key = api_key_for(pid) or ""
    base_url = meta["base_url"]
    if proto.provider_id == "gemini" and not base_url:
        base_url = ""
    return await model_catalog.list_models(meta["protocol"], base_url, api_key)
