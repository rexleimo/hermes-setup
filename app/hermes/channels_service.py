"""消息渠道（platforms）配置服务。

写入规则（与 Hermes 对齐）：
- 启用状态与平台级键 → config.yaml `platforms.<name>.{enabled, ...config_keys, extra}`；
- 令牌等敏感键 → ~/.hermes/.env；
- 每平台工具集覆盖 → config.yaml `platform_toolsets.<name>`。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ruamel.yaml.comments import CommentedMap

from app.hermes.config_store import (
    EnvStore, ensure_path, get_path, load_config, save_config,
)
from app.hermes.paths import detect
from app.hermes.schema import (
    PLATFORMS, PLATFORM_TOOLSET_KEYS, FieldDef, PlatformDef,
)

CLEAR_MARKER = "__CLEAR__"      # 表单里该值 = 删除 .env 键
KEEP_MARKER = ""                # 表单里留空 = 保持现有值不变


class ChannelError(Exception):
    pass


@dataclass
class ChannelView:
    name: str
    definition: PlatformDef
    enabled: bool
    env_state: dict = field(default_factory=dict)      # env_key -> set?（bool）
    extra: dict = field(default_factory=dict)          # platforms.<name>.extra
    config_values: dict = field(default_factory=dict)  # platforms.<name> 直接键
    toolset: str = ""                                  # platform_toolsets 覆盖值
    configured: bool = False                           # 必填凭证（env + extra）是否齐备
    has_required: bool = False                         # 是否存在必填凭证字段

    @property
    def label(self) -> str:
        return self.definition.label

    @property
    def doc(self) -> str:
        return self.definition.doc


def list_channels() -> list[ChannelView]:
    config = load_config()
    env = EnvStore.load()
    platforms_node = get_path(config, "platforms", {}) or {}
    toolsets = get_path(config, "platform_toolsets", {}) or {}

    views: list[ChannelView] = []
    known = set(PLATFORMS) | (set(platforms_node) if isinstance(platforms_node, dict) else set())
    for name in sorted(known):
        d = PLATFORMS.get(name) or PlatformDef(id=name, label=name, verified=False,
                                               note="本版本未收录字段描述，请按官方文档编辑")
        node = platforms_node.get(name, {}) if isinstance(platforms_node, dict) else {}
        node = node if isinstance(node, dict) else CommentedMap()
        extra = node.get("extra", {}) or {}
        extra = dict(extra) if isinstance(extra, dict) else {}

        env_state = {f.name: env.get(f.name) is not None for f in d.env_fields}
        required_ok = all(
            env_state.get(f.name) or not f.required for f in d.env_fields
        )
        # 网关连接判定可能读取 extra 中的必填凭证（如 qqbot app_id/client_secret）
        required_extra_ok = all(
            (not f.required) or str(extra.get(f.name) or "").strip()
            for f in d.extra_fields
        )
        required_ok = required_ok and required_extra_ok
        has_required = (
            any(f.required for f in d.env_fields) or any(f.required for f in d.extra_fields)
        )
        scope_node = config.get(name) if d.config_scope == "top" else node
        if not isinstance(scope_node, dict):
            scope_node = CommentedMap()
        config_values = {
            f.name: _to_str(scope_node.get(f.name, f.default))
            for f in d.config_keys
        }

        views.append(ChannelView(
            name=name, definition=d,
            enabled=bool(node.get("enabled", False)),
            env_state=env_state,
            extra=extra,
            config_values=config_values,
            toolset=str(toolsets.get(name, "") or "") if isinstance(toolsets, dict) else "",
            configured=required_ok,
            has_required=has_required,
        ))
    return views


def get_channel(name: str) -> ChannelView:
    views = list_channels()
    for v in views:
        if v.name == name:
            return v
    raise ChannelError(f"渠道不存在：{name}")


def _to_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return str(v)


def _coerce(field_def: FieldDef, raw: str):
    raw = (raw or "").strip()
    if field_def.kind == "bool":
        return raw.lower() in ("1", "true", "yes", "on")
    if field_def.kind == "int":
        try:
            return int(raw)
        except ValueError:
            return None
    if field_def.kind == "select":
        return raw if raw in field_def.options else None
    return raw or None


def save_channel(name: str, *, enabled: bool | None = None,
                 env_values: dict[str, str] | None = None,
                 config_values: dict[str, str] | None = None,
                 extra_values: dict[str, str] | None = None,
                 extra_json: str | None = None) -> list[str]:
    """应用渠道表单；返回变更说明。空串 = 不改动；__CLEAR__ = 清除该键。"""
    d = PLATFORMS.get(name)
    if d is None:
        raise ChannelError(f"未收录的渠道：{name}")
    changes: list[str] = []
    config = load_config()

    node = ensure_path(config, f"platforms.{name}")
    if enabled is not None:
        was = bool(node.get("enabled", False))
        node["enabled"] = bool(enabled)
        if was != enabled:
            changes.append(f"启用状态：{'开启' if enabled else '关闭'}")

    # .env 敏感键
    env = EnvStore.load()
    for f in d.env_fields:
        raw = (env_values or {}).get(f.name)
        if raw is None or raw == KEEP_MARKER:
            continue
        if raw == CLEAR_MARKER:
            if env.get(f.name) is not None:
                env.set(f.name, "")
                changes.append(f"已清除 .env 键 {f.name}")
            continue
        env.set(f.name, raw.strip())
        changes.append(f"已写入 .env 键 {f.name}")
    if changes and any(c.startswith("已") and ".env" in c for c in changes):
        env.save()
        env.chmod_600()

    # 平台直接键：写入 platforms.<name> 或顶层（discord 等官方定义为顶层的渠道）
    if d.config_scope == "top":
        top_node = ensure_path(config, name)
        for f in d.config_keys:
            raw = (config_values or {}).get(f.name)
            if raw is None or raw == "":
                continue
            coerced = _coerce(f, raw)
            if coerced is None:
                continue
            top_node[f.name] = coerced
            changes.append(f"{name}.{f.name} = {coerced}（顶层）")
    else:
        for f in d.config_keys:
            raw = (config_values or {}).get(f.name)
            if raw is None or raw == "":
                continue
            coerced = _coerce(f, raw)
            if coerced is None:
                continue
            node[f.name] = coerced
            changes.append(f"platforms.{name}.{f.name} = {coerced}")

    # extra 子键
    if extra_values:
        extra = node.get("extra")
        if not isinstance(extra, dict):
            extra = CommentedMap()
            node["extra"] = extra
        for f in d.extra_fields:
            raw = extra_values.get(f.name)
            if raw is None or raw == KEEP_MARKER:
                continue
            if raw == CLEAR_MARKER:
                extra.pop(f.name, None)
                changes.append(f"已移除 extra.{f.name}")
                continue
            coerced = _coerce(f, raw)
            if coerced is None:
                continue
            extra[f.name] = coerced
            changes.append(f"extra.{f.name} 已更新")

    # 高级编辑：extra 全量 JSON（最后应用，覆盖上面的逐字段结果）
    if extra_json is not None and extra_json.strip():
        import json as _json

        try:
            parsed = _json.loads(extra_json)
        except ValueError as exc:
            raise ChannelError(f"高级编辑的 JSON 不合法：{exc}") from exc
        if not isinstance(parsed, dict):
            raise ChannelError("高级编辑内容必须是 JSON 对象（{...}）")
        existing = node.get("extra") if isinstance(node.get("extra"), dict) else {}
        merged = {**existing, **parsed}
        merged = {k: v for k, v in merged.items() if v is not None}  # null = 删除该键
        node["extra"] = merged
        changes.append("extra 已合并写入 JSON 内容（覆盖同名键，null 删除）")

    save_config(config)
    return changes


def set_toolset(name: str, choice: str) -> str:
    """设置 platform_toolsets.<name>；choice 为空 = 移除覆盖。"""
    if name not in PLATFORM_TOOLSET_KEYS:
        raise ChannelError(f"platform_toolsets 不支持渠道：{name}")
    config = load_config()
    node = ensure_path(config, "platform_toolsets")
    if not choice:
        node.pop(name, None)
        action = "已移除工具集覆盖（使用官方默认）"
    else:
        node[name] = [choice]
        action = f"工具集覆盖为 {choice}"
    save_config(config)
    return action


def raw_extra_yaml(name: str) -> str:
    """高级编辑：extra 的内联 YAML 文本。"""
    v = get_channel(name)
    import yaml as _pyyaml  # 仅用于展示格式化，写入仍走结构化路径
    return "" if not v.extra else _pyyaml.safe_dump(v.extra, allow_unicode=True, sort_keys=False)
