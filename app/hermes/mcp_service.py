"""MCP 服务管理：config.yaml `mcp_servers.<name>` 的 CRUD 与官方热门目录。

写入规则（与 Hermes 官方 schema 对齐，
见 hermes-agent/website/docs/reference/mcp-config-reference.md）：
- stdio 型 → command / args / env；http 型 → url / headers（`transport: sse` 可选）；
- 通用键：enabled / timeout / trust（full | untrusted）/ auth: oauth（仅 http）；
- 密钥只写 ~/.hermes/.env（EnvStore，600 权限），config.yaml 中以 `${VAR}` 占位符
  引用 —— Hermes 在连接时解析占位符（官方：先查 profile secret scope，回退进程环境）；
- `agentmemory` 由记忆系统模块托管，本模块只展示不提供删除。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from app.core import db
from app.hermes.config_store import (
    ConfigError, EnvStore, ensure_path, get_path, load_config, save_config,
)
from app.hermes.paths import HermesPaths, detect, resolve_agent_repo

NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,47}$")
TRANSPORTS = ("stdio", "http")   # sse 是 http 的传输开关（transport: sse），不是独立类型
TRUST_LEVELS = ("", "full", "untrusted")
# 官方目录（agent_repo/optional-mcps）中相对更大众化的条目，仅用于排序与「热门」徽标
HOT_MCPS = frozenset({
    "context7", "notion", "linear", "stripe", "supabase", "vercel",
    "sentry", "figma", "deepwiki", "cloudflare", "wolfram", "github",
})


class McpError(Exception):
    pass


# ---------------------------------------------------------------------------
# 视图模型
# ---------------------------------------------------------------------------

@dataclass
class McpServerView:
    name: str
    transport: str                  # stdio | http | sse
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    timeout: int | None = None
    trust: str = "full"
    auth_oauth: bool = False
    note: str = ""

    @property
    def target(self) -> str:
        return self.command if self.transport == "stdio" else self.url

    @property
    def secret_env_keys(self) -> list[str]:
        """env 中引用 ${VAR} 占位符的键（值本体在 .env）。"""
        return sorted(k for k, v in self.env.items() if _is_secret_ref(v))

    @property
    def secret_header_keys(self) -> list[str]:
        return sorted(k for k, v in self.headers.items() if _is_secret_ref(v))

    @property
    def managed_label(self) -> str:
        return "记忆系统托管" if self.name == "agentmemory" else ""

    # ---- 表单回显辅助 -----------------------------------------------------

    @property
    def env_plain(self) -> dict[str, str]:
        return {k: v for k, v in self.env.items() if not _is_secret_ref(v)}

    @property
    def env_secrets(self) -> dict[str, str]:
        return {k: v for k, v in self.env.items() if _is_secret_ref(v)}

    @property
    def headers_plain(self) -> dict[str, str]:
        return {k: v for k, v in self.headers.items() if not _is_secret_ref(v)}

    @property
    def headers_secrets(self) -> dict[str, str]:
        return {k: v for k, v in self.headers.items() if _is_secret_ref(v)}

    @property
    def env_plain_text(self) -> str:
        return "\n".join(f"{k}={v}" for k, v in self.env_plain.items())

    @property
    def headers_plain_text(self) -> str:
        return "\n".join(f"{k}={v}" for k, v in self.headers_plain.items())

    @property
    def args_text(self) -> str:
        return "\n".join(self.args)


def _is_secret_ref(value: str) -> bool:
    return isinstance(value, str) and value.startswith("${") and value.endswith("}")


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def list_servers(paths: HermesPaths | None = None) -> list[McpServerView]:
    config = load_config(paths)
    raw = get_path(config, "mcp_servers", {})
    metas = {m["name"]: m for m in db.query("SELECT * FROM mcp_meta")}
    views: list[McpServerView] = []
    if isinstance(raw, dict):
        for name, node in raw.items():
            if not isinstance(node, dict):
                continue
            views.append(_view_from_node(str(name), node, metas.get(str(name))))
    views.sort(key=lambda v: v.name)
    return views


def _view_from_node(name: str, node: dict, meta) -> McpServerView:
    url = str(node.get("url") or "")
    transport = "stdio" if node.get("command") else "http"
    if url and node.get("transport") == "sse":
        transport = "sse"
    timeout = node.get("timeout")
    return McpServerView(
        name=name,
        transport=transport,
        command=str(node.get("command") or ""),
        args=[str(a) for a in node.get("args", [])] if isinstance(node.get("args"), list) else [],
        env={str(k): str(v) for k, v in (node.get("env") or {}).items()},
        url=url,
        headers={str(k): str(v) for k, v in (node.get("headers") or {}).items()},
        enabled=bool(node.get("enabled", True)),
        timeout=int(timeout) if isinstance(timeout, (int, float)) else None,
        trust=str(node.get("trust") or "full"),
        auth_oauth=str(node.get("auth") or "") == "oauth",
        note=meta["note"] if meta else "",
    )


def get_server(name: str, paths: HermesPaths | None = None) -> McpServerView:
    for v in list_servers(paths):
        if v.name == name:
            return v
    raise McpError(f"MCP 服务不存在：{name}")


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def _kv_from_text(text: str) -> dict[str, str]:
    """把多行 `KEY=value` 文本解析成映射；空行与 # 注释忽略。"""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key:
            out[key] = value.strip()
    return out


def _args_from_text(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _env_var_name(server: str, key: str) -> str:
    tail = re.sub(r"[^A-Za-z0-9]", "_", key).upper()
    return f"MCP_{re.sub(r'[^A-Za-z0-9]', '_', server).upper()}_{tail}"


@dataclass
class McpServerInput:
    """表单原始输入：env/headers 明文键值与密钥键值分开提交。"""

    transport: str                  # stdio | http
    use_sse: bool = False           # http 类型下启用 SSE 传输（transport: sse）
    command: str = ""
    args_text: str = ""
    env_text: str = ""
    url: str = ""
    headers_text: str = ""
    enabled: bool = True
    timeout: str = ""
    trust: str = "full"
    auth_oauth: bool = False
    secret_env_text: str = ""       # 密钥：写入 .env，config 引用 ${VAR}
    secret_headers_text: str = ""


def _validate(name: str, inp: McpServerInput) -> None:
    if not NAME_RE.match(name):
        raise McpError("名称需为字母开头、1-48 位字母/数字/连字符/下划线")
    if inp.transport not in TRANSPORTS:
        raise McpError("传输类型必须是 stdio / http")
    if inp.transport == "stdio" and not inp.command.strip():
        raise McpError("stdio 类型必须填写启动命令")
    if inp.transport == "http":
        url = inp.url.strip()
        if not url.startswith(("http://", "https://")):
            raise McpError("URL 必须以 http:// 或 https:// 开头")
    if inp.trust not in TRUST_LEVELS:
        raise McpError("信任级别只能是 full / untrusted")
    if inp.timeout.strip() and not inp.timeout.strip().isdigit():
        raise McpError("超时秒数必须是正整数")
    if inp.timeout.strip() and not (1 <= int(inp.timeout.strip()) <= 3600):
        raise McpError("超时秒数需在 1-3600 之间")


def create_server(name: str, inp: McpServerInput,
                  paths: HermesPaths | None = None) -> list[str]:
    name = name.strip()
    _validate(name, inp)
    paths = paths or detect()
    if db.query_one("SELECT 1 FROM mcp_meta WHERE name = ?", (name,)) or _exists_in_config(name, paths):
        raise McpError(f"同名 MCP 服务已存在：{name}")
    notes = _write_server_node(name, inp, paths, preserve_secret_refs=False)
    db.execute("INSERT INTO mcp_meta (name, note) VALUES (?,?)", (name, ""))
    return notes


def update_server(name: str, inp: McpServerInput,
                  paths: HermesPaths | None = None) -> list[str]:
    _validate(name, inp)
    paths = paths or detect()
    if not _exists_in_config(name, paths):
        raise McpError(f"MCP 服务不存在：{name}")
    return _write_server_node(name, inp, paths, preserve_secret_refs=True)


def _exists_in_config(name: str, paths: HermesPaths) -> bool:
    servers = get_path(load_config(paths), "mcp_servers", {})
    return isinstance(servers, dict) and name in servers


def _write_server_node(name: str, inp: McpServerInput, paths: HermesPaths,
                       *, preserve_secret_refs: bool) -> list[str]:
    notes: list[str] = []
    config = load_config(paths)
    node = ensure_path(config, f"mcp_servers.{name}")

    # 更新时先记住存量密钥引用（${VAR}），重建后原样带回 —— 表单里不含密钥明文
    kept_env_refs: dict[str, str] = {}
    kept_header_refs: dict[str, str] = {}
    if preserve_secret_refs:
        kept_env_refs = {k: v for k, v in (node.get("env") or {}).items()
                         if _is_secret_ref(str(v))}
        kept_header_refs = {k: v for k, v in (node.get("headers") or {}).items()
                            if _is_secret_ref(str(v))}

    # 先清掉与当前传输类型无关的旧键，避免 stdio/http 来回切时残留
    for stale in ("command", "args", "url", "headers", "env", "transport", "auth"):
        node.pop(stale, None)

    if inp.transport == "stdio":
        node["command"] = inp.command.strip()
        args = _args_from_text(inp.args_text)
        if args:
            node["args"] = args
    else:
        node["url"] = inp.url.strip()
        if inp.use_sse:
            node["transport"] = "sse"
        if inp.auth_oauth:
            node["auth"] = "oauth"

    env = _kv_from_text(inp.env_text)
    if env:
        node["env"] = CommentedMap(env)

    headers = _kv_from_text(inp.headers_text)
    if headers:
        node["headers"] = CommentedMap(headers)

    # 密钥键值 → .env + ${VAR} 引用
    env_store = EnvStore.load(paths)
    for key, value in _kv_from_text(inp.secret_env_text).items():
        var = _env_var_name(name, key)
        env_store.set(var, value)
        notes.append(f"密钥 {key} 已写入 .env（{var}）")
        node.setdefault("env", CommentedMap())[key] = "${" + var + "}"
    for key, value in _kv_from_text(inp.secret_headers_text).items():
        var = _env_var_name(name, key)
        env_store.set(var, value)
        notes.append(f"Header 密钥 {key} 已写入 .env（{var}）")
        node.setdefault("headers", CommentedMap())[key] = "${" + var + "}"
    if notes:
        env_store.save()
        env_store.chmod_600()

    for key, ref in kept_env_refs.items():
        node.setdefault("env", CommentedMap()).setdefault(key, ref)
    for key, ref in kept_header_refs.items():
        node.setdefault("headers", CommentedMap()).setdefault(key, ref)

    if not inp.enabled:
        node["enabled"] = False
    else:
        node.pop("enabled", None)   # 默认即启用，不落键
    if inp.timeout.strip():
        node["timeout"] = int(inp.timeout.strip())
    else:
        node.pop("timeout", None)
    if inp.trust == "untrusted":
        node["trust"] = "untrusted"
    else:
        node.pop("trust", None)     # 默认 full，不落键

    save_config(config, paths)
    notes.append(f"mcp_servers.{name} 已写入 config.yaml")
    return notes


def set_enabled(name: str, enabled: bool, paths: HermesPaths | None = None) -> None:
    paths = paths or detect()
    config = load_config(paths)
    servers = get_path(config, "mcp_servers", None)
    if not isinstance(servers, dict) or name not in servers:
        raise McpError(f"MCP 服务不存在：{name}")
    node = servers[name]
    if enabled:
        node.pop("enabled", None)
    else:
        node["enabled"] = False
    save_config(config, paths)


def delete_server(name: str, paths: HermesPaths | None = None) -> list[str]:
    if name == "agentmemory":
        raise McpError("agentmemory 由记忆系统模块托管，请到「记忆系统」页操作")
    paths = paths or detect()
    config = load_config(paths)
    servers = get_path(config, "mcp_servers", None)
    if not isinstance(servers, dict) or name not in servers:
        raise McpError(f"MCP 服务不存在：{name}")

    notes: list[str] = []
    node = servers.pop(name)
    save_config(config, paths)
    notes.append(f"已从 config.yaml 移除 mcp_servers.{name}")

    # 顺带清理该服务写入 .env 的密钥变量
    env = EnvStore.load(paths)
    refs = _collect_secret_refs(node)
    removed = 0
    for var in refs:
        if env.get(var) is not None:
            env.set(var, "")
            removed += 1
    if removed:
        env.save()
        notes.append(f"已从 .env 删除 {removed} 个密钥变量")

    db.execute("DELETE FROM mcp_meta WHERE name = ?", (name,))
    return notes


def _collect_secret_refs(node) -> list[str]:
    import re as _re

    out: list[str] = []
    for section in ("env", "headers"):
        mapping = node.get(section) if isinstance(node, dict) else None
        if isinstance(mapping, dict):
            for value in mapping.values():
                m = _re.match(r"^\$\{(?:env:)?([A-Za-z_][A-Za-z0-9_]*)\}$", str(value))
                if m:
                    out.append(m.group(1))
    return out


def set_note(name: str, note: str) -> None:
    if db.query_one("SELECT 1 FROM mcp_meta WHERE name = ?", (name,)):
        db.execute("UPDATE mcp_meta SET note = ?, updated_at = datetime('now') WHERE name = ?",
                   (note.strip(), name))
    else:
        db.execute("INSERT INTO mcp_meta (name, note) VALUES (?,?)", (name, note.strip()))


# ---------------------------------------------------------------------------
# 官方热门目录（agent_repo/optional-mcps/*/manifest.yaml）
# ---------------------------------------------------------------------------

@dataclass
class McpCatalogEntry:
    id: str                         # 目录名，即安装后的 server 名
    description: str
    transport: str                  # stdio | http
    url: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    auth_type: str = "none"         # none | oauth | ...
    source: str = ""
    post_install: str = ""
    hot: bool = False

    @property
    def transport_label(self) -> str:
        return {"stdio": "本地进程", "http": "远程 HTTP"}.get(self.transport, self.transport)


def catalog_dir(paths: HermesPaths | None = None) -> Path:
    paths = paths or detect()
    return (resolve_agent_repo(paths) or paths.agent_repo) / "optional-mcps"


def catalog(paths: HermesPaths | None = None) -> list[McpCatalogEntry]:
    base = catalog_dir(paths)
    if not base.is_dir():
        return []
    from ruamel.yaml import YAML

    yaml = YAML(typ="safe", pure=True)
    entries: list[McpCatalogEntry] = []
    for manifest in sorted(base.glob("*/manifest.yaml")):
        try:
            data = yaml.load(manifest.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        cid = str(data.get("name") or manifest.parent.name)
        transport_node = data.get("transport") or {}
        if not isinstance(transport_node, dict):
            transport_node = {}
        ttype = str(transport_node.get("type") or "http")
        entry = McpCatalogEntry(
            id=cid,
            description=str(data.get("description") or "").strip(),
            transport=ttype,
            url=str(transport_node.get("url") or ""),
            command=str(transport_node.get("command") or ""),
            args=[str(a) for a in transport_node.get("args", [])]
            if isinstance(transport_node.get("args"), list) else [],
            auth_type=str((data.get("auth") or {}).get("type") or "none")
            if isinstance(data.get("auth"), dict) else str(data.get("auth") or "none"),
            source=str(data.get("source") or ""),
            post_install=str(data.get("post_install") or "").strip(),
            hot=cid in HOT_MCPS,
        )
        entries.append(entry)
    # 热门优先，其余按 id 排序
    entries.sort(key=lambda e: (not e.hot, e.id))
    return entries


def catalog_entry(cid: str, paths: HermesPaths | None = None) -> McpCatalogEntry:
    for e in catalog(paths):
        if e.id == cid:
            return e
    raise McpError(f"官方目录中不存在：{cid}")


def install_from_catalog(cid: str, paths: HermesPaths | None = None) -> list[str]:
    """把官方目录条目一键写入 mcp_servers（与 `hermes mcp install` 等效的配置结果）。"""
    entry = catalog_entry(cid, paths)
    if _exists_in_config(entry.id, paths or detect()):
        raise McpError(f"{entry.id} 已在 mcp_servers 中")
    # 与官方 `hermes mcp install` 行为对齐：只写传输配置，不落 trust 键（默认 full）
    inp = McpServerInput(
        transport="stdio" if entry.transport == "stdio" else "http",
        command=entry.command,
        args_text="\n".join(entry.args),
        url=entry.url,
        auth_oauth=entry.auth_type == "oauth",
    )
    notes = create_server(entry.id, inp, paths)
    db.execute("UPDATE mcp_meta SET note = ? WHERE name = ?", (f"来自官方目录：{entry.id}", entry.id))
    if entry.post_install:
        notes.append(f"安装提示：{entry.post_install}")
    return notes
