"""记忆系统管理：内置记忆调优 + 外置记忆方案（provider）自动配置。

背景（官方文档与社区方案，2026-09）：
- 内置记忆 MEMORY.md/USER.md 默认仅 2,200/1,375 字符（约 1,300 token），
  大项目下容量紧张，写入超限会直接报错；
- 外置方案通过 `memory.provider` 启用，同一时刻只能激活一个，内置记忆仍然并存；
- 社区方案 agentmemory（github.com/rohitg00/agentmemory）提供跨 Agent 记忆共享与
  混合检索，接入方式 A：MCP（零代码）；方式 B：Provider 插件（深度集成）。

本模块把「选方案 → 填参数 → 写配置」收敛为一次表单提交：
写 config.yaml（memory.* / mcp_servers / plugins.*）+ 各 provider 的 JSON 配置文件 + .env 密钥。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ruamel.yaml.comments import CommentedMap

from app.core import db
from app.hermes.config_store import (
    EnvStore, ensure_path, get_path, load_config, load_json_opt, save_config,
    save_json,
)
from app.hermes.paths import HermesPaths, detect
from app.hermes.schema import FieldDef

PROVIDER_CONFIG_FILES = {
    "honcho": "honcho.json",
    "mem0": "mem0.json",
    "supermemory": "supermemory.json",
    "hindsight": "hindsight/config.json",
}

# 内置记忆容量预设（label -> (memory_char_limit, user_char_limit)）
BUILTIN_PRESETS: dict[str, tuple[int, int]] = {
    "官方默认": (2200, 1375),
    "进阶": (8000, 4000),
    "重度使用": (16000, 8000),
}


class MemoryError(Exception):
    pass


# ---------------------------------------------------------------------------
# 状态
# ---------------------------------------------------------------------------

@dataclass
class MemoryStatus:
    provider: str = ""                       # memory.provider；空 = 仅内置
    builtin: dict = field(default_factory=dict)
    context_file_max_chars: int | None = None
    mcp_agentmemory: bool = False
    plugin_agentmemory: bool = False
    config_files: dict = field(default_factory=dict)   # name -> bool
    memory_files: dict = field(default_factory=dict)   # MEMORY.md/USER.md -> 字符数


def _builtin_values(config: CommentedMap) -> dict:
    return {
        "memory_enabled": bool(get_path(config, "memory.memory_enabled", True)),
        "user_profile_enabled": bool(get_path(config, "memory.user_profile_enabled", True)),
        "memory_char_limit": get_path(config, "memory.memory_char_limit", 2200),
        "user_char_limit": get_path(config, "memory.user_char_limit", 1375),
        "nudge_interval": get_path(config, "memory.nudge_interval", 10),
        "write_approval": bool(get_path(config, "memory.write_approval", False)),
    }


def get_status(paths: HermesPaths | None = None) -> MemoryStatus:
    paths = paths or detect()
    config = load_config(paths)
    st = MemoryStatus(
        provider=str(get_path(config, "memory.provider", "") or ""),
        builtin=_builtin_values(config),
        context_file_max_chars=get_path(config, "context_file_max_chars", None),
    )
    mcp = get_path(config, "mcp_servers.agentmemory", None)
    st.mcp_agentmemory = isinstance(mcp, dict)
    st.plugin_agentmemory = (paths.home / "plugins" / "memory" / "agentmemory").is_dir()
    for name, rel in PROVIDER_CONFIG_FILES.items():
        st.config_files[name] = bool(load_json_opt(paths, rel))
    for filename in ("MEMORY.md", "USER.md"):
        p = paths.home / filename
        st.memory_files[filename] = p.stat().st_size if p.exists() else 0
    return st


# ---------------------------------------------------------------------------
# 内置记忆调优
# ---------------------------------------------------------------------------

def save_builtin(*, memory_enabled: bool, user_profile_enabled: bool,
                 memory_char_limit: int, user_char_limit: int,
                 nudge_interval: int, write_approval: bool,
                 context_file_max_chars: int | None) -> list[str]:
    if memory_char_limit < 500 or memory_char_limit > 200_000:
        raise MemoryError("memory_char_limit 需在 500 – 200,000 之间")
    if user_char_limit < 300 or user_char_limit > 100_000:
        raise MemoryError("user_char_limit 需在 300 – 100,000 之间")
    config = load_config()
    node = ensure_path(config, "memory")
    node["memory_enabled"] = bool(memory_enabled)
    node["user_profile_enabled"] = bool(user_profile_enabled)
    node["memory_char_limit"] = int(memory_char_limit)
    node["user_char_limit"] = int(user_char_limit)
    node["nudge_interval"] = max(0, int(nudge_interval))
    node["write_approval"] = bool(write_approval)
    if context_file_max_chars:
        if not (20_000 <= context_file_max_chars <= 500_000):
            raise MemoryError("context_file_max_chars 官方允许区间为 20,000 – 500,000")
        config["context_file_max_chars"] = int(context_file_max_chars)
    else:
        config.pop("context_file_max_chars", None)
    save_config(config)
    return ["memory.* 已更新", "context_file_max_chars 已更新"]


# ---------------------------------------------------------------------------
# 外置方案：描述符
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MemoryProviderDef:
    id: str
    label: str
    emoji: str
    tagline: str
    best_for: str
    requires: str
    storage: str
    cost: str
    docs: str = ""
    form_fields: tuple[FieldDef, ...] = ()
    setup_hint: str = ""
    mcp_capable: bool = False       # 提供 agentmemory MCP 形态
    job_command: str = ""           # 可选的依赖安装任务（静态命令）
    job_label: str = ""
    deps_pip: tuple[str, ...] = ()  # 需要装入 Hermes 环境的 Python 包
    deps_dynamic: bool = False      # 依赖随表单取值变化（如 hindsight local/cloud）


AGENTMEMORY_PLUGIN_JOB = (
    'curl -fsSL https://github.com/rohitg00/agentmemory/archive/refs/heads/main.tar.gz'
    ' -o /tmp/agentmemory.tgz && tar -xzf /tmp/agentmemory.tgz -C /tmp'
    ' && mkdir -p "$HOME/.hermes/plugins/memory"'
    ' && rm -rf "$HOME/.hermes/plugins/memory/agentmemory"'
    ' && cp -r /tmp/agentmemory-main/integrations/hermes "$HOME/.hermes/plugins/memory/agentmemory"'
    ' && rm -rf /tmp/agentmemory-main /tmp/agentmemory.tgz && echo "plugin installed"'
)
BYTEROVER_CLI_JOB = "curl -fsSL https://byterover.dev/install.sh | sh"

MEMORY_PROVIDERS: dict[str, MemoryProviderDef] = {
    "agentmemory": MemoryProviderDef(
        id="agentmemory", label="AgentMemory", emoji="🧠",
        tagline="社区方案 · 跨 Agent 共享记忆 + 混合检索",
        best_for="同时使用 Claude Code / Cursor 等工具、想要统一记忆与结构化检索的团队",
        requires="Node.js（npx）或已部署的 agentmemory 服务",
        storage="本地 / 自建服务端", cost="免费开源",
        docs="https://github.com/rohitg00/agentmemory",
        mcp_capable=True,
        form_fields=(
            FieldDef("mode", "接入方式", kind="select",
                     options=("mcp", "plugin"), default="mcp",
                     help="mcp = 零代码（Hermes 经 MCP 启动，官方 schema 原生支持）；"
                          "plugin = 深度 Provider 插件（自动下载到 plugins/memory/agentmemory）"),
            FieldDef("deployment", "运行位置（MCP 模式）", kind="select",
                     options=("local", "remote"), default="local",
                     visible_if_field="mode", visible_if_value="mcp",
                     help="本机：npx 自动运行，记忆数据只存本机，无需任何服务端；"
                          "已有服务端：连接你独立部署的 agentmemory（团队共享记忆场景）"),
            FieldDef("agentmemory_api_url", "服务端地址", visible_if_field="deployment",
                     visible_if_value="remote",
                     help="已有服务端的地址，例如 http://10.0.0.9:3113"),
        ),
        job_command=AGENTMEMORY_PLUGIN_JOB,
        job_label="下载并安装 agentmemory Provider 插件",
    ),
    "mem0": MemoryProviderDef(
        id="mem0", label="Mem0", emoji="📒", deps_pip=("mem0ai",),
        tagline="服务端 LLM 事实抽取 + 语义检索 + 自动去重",
        best_for="想完全托管记忆管理（Platform 云 / 自建 Docker 服务端）",
        requires="mem0ai（pip）+ API Key（Platform）或自建服务端",
        storage="Mem0 云 / 自建", cost="云付费 / 自建免费",
        form_fields=(
            FieldDef("mode", "连接模式", kind="select",
                     options=("platform", "selfhosted"), default="platform",
                     help="oss 模式需要自备 LLM 与向量库，请使用官方 `hermes memory setup mem0 --mode oss` 向导"),
            FieldDef("MEM0_API_KEY", "API Key", kind="password", required=True,
                     help="Platform 为云密钥；selfhosted 为服务端管理 Key"),
            FieldDef("host", "服务端地址（selfhosted）", visible_if_field="mode",
                     visible_if_value="selfhosted", help="例如 http://localhost:8888"),
            FieldDef("user_id", "用户标识", default="hermes-user"),
        ),
    ),
    "supermemory": MemoryProviderDef(
        id="supermemory", label="Supermemory", emoji="✨", deps_pip=("supermemory",),
        tagline="语义长期记忆 + 用户画像召回 + 会话级图存取",
        best_for="语义召回、跨会话用户画像",
        requires="supermemory（pip）+ 云 API Key，或自建服务端（npx supermemory local）",
        storage="云 / 自建", cost="云付费 / 自建免费",
        form_fields=(
            FieldDef("SUPERMEMORY_API_KEY", "API Key", kind="password", required=True),
            FieldDef("base_url", "服务端地址（自建，可选）",
                     help="留空 = 官方云 https://api.supermemory.ai"),
            FieldDef("container_tag", "容器标签", default="hermes"),
            FieldDef("auto_recall", "自动召回", kind="bool", default="true"),
            FieldDef("auto_capture", "自动捕获对话", kind="bool", default="true"),
        ),
    ),
    "openviking": MemoryProviderDef(
        id="openviking", label="OpenViking", emoji="🌪️", deps_pip=("openviking",),
        tagline="字节火山引擎上下文数据库 · 文件系统式知识层级 + 分层检索",
        best_for="自托管的结构化知识管理（L0/L1/L2 分层加载）",
        requires="openviking-server 初始化并运行中",
        storage="自托管", cost="免费开源（AGPL-3.0）",
        form_fields=(
            FieldDef("OPENVIKING_ENDPOINT", "服务端地址", required=True,
                     default="http://127.0.0.1:1933"),
            FieldDef("OPENVIKING_API_KEY", "API Key（可选）", kind="password"),
            FieldDef("OPENVIKING_ACCOUNT", "Account（本地信任模式）", default="default"),
            FieldDef("OPENVIKING_USER", "User（本地信任模式）", default="default"),
        ),
    ),
    "hindsight": MemoryProviderDef(
        id="hindsight", label="Hindsight", emoji="🔎",
        deps_pip=("hindsight-client",), deps_dynamic=True,
        tagline="知识图谱 + 实体消解 + reflect 跨记忆综合",
        best_for="需要实体关系推理与多策略召回",
        requires="云 API Key 或本地模式（本地需 LLM Key）",
        storage="云 / 本地内嵌 PostgreSQL", cost="云付费 / 本地免费",
        form_fields=(
            FieldDef("HINDSIGHT_API_KEY", "API Key（云模式必填）", kind="password"),
            FieldDef("mode", "运行模式", kind="select", options=("cloud", "local"),
                     default="cloud"),
            FieldDef("bank_id", "记忆库标识", default="hermes"),
            FieldDef("recall_budget", "召回力度", kind="select",
                     options=("low", "mid", "high"), default="mid"),
            FieldDef("memory_mode", "注入模式", kind="select",
                     options=("hybrid", "context", "tools"), default="hybrid"),
        ),
    ),
    "holographic": MemoryProviderDef(
        id="holographic", label="Holographic", emoji="🔮",
        tagline="本地 SQLite 事实库 + FTS5 + 信任评分 + HRR 代数查询",
        best_for="纯本地、零外部依赖的高级检索",
        requires="无（SQLite 内置）",
        storage="本地 SQLite", cost="免费",
        form_fields=(
            FieldDef("auto_extract", "会话结束自动抽取事实", kind="bool"),
            FieldDef("default_trust", "默认信任分（0.0–1.0）", default="0.5"),
        ),
    ),
    "retaindb": MemoryProviderDef(
        id="retaindb", label="RetainDB", emoji="🗃️",
        tagline="混合检索（向量 + BM25 + 重排）+ 7 种记忆类型 + 增量压缩",
        best_for="已在用 RetainDB 基础设施的团队",
        requires="RetainDB 账号 + API Key",
        storage="RetainDB 云", cost="$20/月",
        form_fields=(
            FieldDef("RETAINDB_API_KEY", "API Key", kind="password", required=True),
        ),
    ),
    "byterover": MemoryProviderDef(
        id="byterover", label="ByteRover", emoji="🌿",
        tagline="brv CLI 知识树 + 分层检索 + 压缩前自动抽取",
        best_for="想要可移植、本地优先、带 CLI 的记忆",
        requires="ByteRover CLI（可一键安装）",
        storage="本地 / 云同步可选", cost="本地免费",
        job_command=BYTEROVER_CLI_JOB,
        job_label="安装 ByteRover CLI（byterover.dev 官方脚本）",
        form_fields=(),
    ),
    "honcho": MemoryProviderDef(
        id="honcho", label="Honcho", emoji="🎭", deps_pip=("honcho-ai",),
        tagline="AI 原生跨会话用户建模 + 辩证推理 + 会话级上下文",
        best_for="多 Agent 系统的用户-代理对齐建模",
        requires="honcho-ai（pip）+ 云 API Key 或自建实例",
        storage="Honcho 云 / 自建", cost="云付费 / 自建免费",
        form_fields=(
            FieldDef("apiKey", "API Key（云模式）", kind="password",
                     help="自建实例可留空，改填服务端地址"),
            FieldDef("baseUrl", "服务端地址（自建，可选）"),
            FieldDef("peerName", "用户 Peer 标识", required=True),
            FieldDef("workspace", "共享 Workspace", default="hermes"),
        ),
    ),
}


def list_providers() -> list[MemoryProviderDef]:
    return list(MEMORY_PROVIDERS.values())


def get_provider_def(pid: str) -> MemoryProviderDef:
    if pid not in MEMORY_PROVIDERS:
        raise MemoryError(f"未知记忆方案：{pid}")
    return MEMORY_PROVIDERS[pid]


# ---------------------------------------------------------------------------
# 应用外置方案
# ---------------------------------------------------------------------------

def _set_provider(config: CommentedMap, pid: str | None) -> None:
    node = ensure_path(config, "memory")
    if pid:
        node["provider"] = pid
    else:
        node.pop("provider", None)


def _read_form(form, d: MemoryProviderDef) -> dict:
    out = {}
    for f in d.form_fields:
        raw = form.get(f.name)
        if raw is None:
            continue
        raw = str(raw).strip()
        if f.kind == "bool":
            out[f.name] = raw.lower() in ("1", "true", "on", "yes")
        elif f.kind == "int":
            out[f.name] = int(raw) if raw.isdigit() else None
        else:
            out[f.name] = raw
    return out


def apply_provider(pid: str, form: dict) -> list[str]:
    """按方案写入全部所需配置；返回变更说明（供审计与 toast）。"""
    d = get_provider_def(pid)
    values = _read_form(form, d)
    _require(values, d)
    changes: list[str] = []
    config = load_config()

    if pid == "agentmemory":
        changes += _apply_agentmemory(config, values)
    elif pid == "mem0":
        changes += _apply_mem0(config, values)
    elif pid == "supermemory":
        changes += _apply_supermemory(config, values)
    elif pid == "openviking":
        changes += _apply_env_provider(config, d, values, "openviking")
    elif pid == "hindsight":
        changes += _apply_hindsight(config, values)
    elif pid == "holographic":
        changes += _apply_holographic(config, values)
    elif pid == "retaindb":
        changes += _apply_env_provider(config, d, values, "retaindb")
    elif pid == "byterover":
        _set_provider(config, "byterover")
        changes.append("memory.provider = byterover")
    elif pid == "honcho":
        changes += _apply_honcho(config, values)
    else:  # pragma: no cover
        raise MemoryError(f"方案未实现：{pid}")

    save_config(config)
    return changes


def _require(values: dict, d: MemoryProviderDef) -> None:
    for f in d.form_fields:
        if f.required and not values.get(f.name):
            raise MemoryError(f"缺少必填项：{f.label}")


def _write_env(**kv: str) -> None:
    env = EnvStore.load()
    touched = False
    for key, value in kv.items():
        if value:
            env.set(key, value)
            touched = True
    if touched:
        env.save()
        env.chmod_600()


def _apply_agentmemory(config: CommentedMap, values: dict) -> list[str]:
    changes: list[str] = []
    mode = values.get("mode", "mcp")
    mcp_node = ensure_path(config, "mcp_servers")
    if mode == "mcp":
        deployment = values.get("deployment", "local")
        if deployment == "remote" and not values.get("agentmemory_api_url"):
            raise MemoryError("连接已有服务端需要填写服务端地址（或改回「本机」模式）")
        entry = CommentedMap()
        entry["command"] = "npx"
        entry["args"] = ["agentmemory-mcp"]
        api_url = values.get("agentmemory_api_url") if deployment == "remote" else ""
        if api_url:
            entry["env"] = {"AGENTMEMORY_API_URL": api_url}
        mcp_node["agentmemory"] = entry
        changes.append("mcp_servers.agentmemory 已写入（MCP 零代码接入）")
    else:
        if "agentmemory" in mcp_node:
            mcp_node.pop("agentmemory")
            changes.append("已移除 mcp_servers.agentmemory（切换为插件形态）")
    _set_provider(config, "agentmemory")
    changes.append("memory.provider = agentmemory（插件目录由安装任务写入 plugins/memory/agentmemory）")
    return changes


def _apply_mem0(config: CommentedMap, values: dict) -> list[str]:
    mode = values.get("mode", "platform")
    updates: dict = {"user_id": values.get("user_id") or "hermes-user"}
    if mode == "selfhosted":
        if not values.get("host"):
            raise MemoryError("自建服务端模式需要填写服务端地址")
        updates["host"] = values["host"]
        if values.get("MEM0_API_KEY"):
            updates["api_key"] = values["MEM0_API_KEY"]
    else:
        updates.pop("host", None)
    save_json(None, "mem0.json", updates)
    _write_env(**{"MEM0_API_KEY": values.get("MEM0_API_KEY", "")})
    _set_provider(config, "mem0")
    return [f"mem0.json 已写入（{mode} 模式）", ".env 写入 MEM0_API_KEY",
            "memory.provider = mem0"]


def _apply_supermemory(config: CommentedMap, values: dict) -> list[str]:
    updates: dict = {}
    if values.get("base_url"):
        updates["base_url"] = values["base_url"]
    if values.get("container_tag"):
        updates["container_tag"] = values["container_tag"]
    for key in ("auto_recall", "auto_capture"):
        if key in values:
            updates[key] = bool(values[key])
    if updates:
        save_json(None, "supermemory.json", updates)
    _write_env(**{"SUPERMEMORY_API_KEY": values.get("SUPERMEMORY_API_KEY", "")})
    _set_provider(config, "supermemory")
    return ["supermemory.json 已更新" if updates else "supermemory.json 未变更",
            ".env 写入 SUPERMEMORY_API_KEY", "memory.provider = supermemory"]


def _apply_env_provider(config: CommentedMap, d: MemoryProviderDef,
                        values: dict, pid: str) -> list[str]:
    # 该形态的全部配置都是 .env 环境变量（全大写命名字段）
    kv = {f.name: str(values.get(f.name) or "") for f in d.form_fields
          if f.name == f.name.upper()}
    kv = {k: v for k, v in kv.items() if v}
    _write_env(**kv)
    _set_provider(config, pid)
    return [f".env 写入 {', '.join(kv)}" if kv else ".env 无变更",
            f"memory.provider = {pid}"]


def _apply_hindsight(config: CommentedMap, values: dict) -> list[str]:
    updates = {
        "mode": values.get("mode", "cloud"),
        "bank_id": values.get("bank_id") or "hermes",
        "recall_budget": values.get("recall_budget", "mid"),
        "memory_mode": values.get("memory_mode", "hybrid"),
    }
    save_json(None, "hindsight/config.json", updates)
    _write_env(**{"HINDSIGHT_API_KEY": values.get("HINDSIGHT_API_KEY", "")})
    _set_provider(config, "hindsight")
    return ["hindsight/config.json 已写入", ".env 写入 HINDSIGHT_API_KEY",
            "memory.provider = hindsight"]


def _apply_holographic(config: CommentedMap, values: dict) -> list[str]:
    node = ensure_path(config, "plugins.hermes-memory-store")
    if "auto_extract" in values:
        node["auto_extract"] = bool(values["auto_extract"])
    trust = values.get("default_trust")
    if trust:
        try:
            node["default_trust"] = max(0.0, min(1.0, float(trust)))
        except ValueError:
            pass
    _set_provider(config, "holographic")
    return ["plugins.hermes-memory-store 已写入", "memory.provider = holographic"]


def _apply_honcho(config: CommentedMap, values: dict) -> list[str]:
    host_block: dict = {"enabled": True, "aiPeer": "hermes"}
    if values.get("peerName"):
        host_block["peerName"] = values["peerName"]
    if values.get("workspace"):
        host_block["workspace"] = values["workspace"]
    updates: dict = {"hosts": {"hermes": host_block}}
    if values.get("apiKey"):
        updates["apiKey"] = values["apiKey"]
    if values.get("baseUrl"):
        updates["baseUrl"] = values["baseUrl"]
    save_json(None, "honcho.json", updates)
    _set_provider(config, "honcho")
    return ["honcho.json 已写入（保留既有配置合并更新）", "memory.provider = honcho"]


def disable_provider(keep_mcp: bool = True) -> list[str]:
    """关闭外置方案，回到纯内置记忆。"""
    config = load_config()
    _set_provider(config, None)
    changes = ["已移除 memory.provider（回到纯内置记忆）"]
    if not keep_mcp:
        mcp_node = get_path(config, "mcp_servers", None)
        if isinstance(mcp_node, dict) and "agentmemory" in mcp_node:
            mcp_node.pop("agentmemory")
            changes.append("已移除 mcp_servers.agentmemory（AgentMemory MCP 配置）")
    save_config(config)
    return changes


# ---------------------------------------------------------------------------
# 依赖安装任务：把 Provider 所需的 Python 包装进 Hermes 的运行环境
# ---------------------------------------------------------------------------

PIP_RESOLVE = (
    'PIP=""\n'
    'for c in "$HOME/.hermes/hermes-agent/.venv/bin/pip"'
    ' "$HOME/.hermes/hermes-agent/venv/bin/pip"; do'
    ' [ -x "$c" ] && PIP="$c" && break; done\n'
    '[ -z "$PIP" ] && PIP="pip3"\n'
)


def deps_command(pid: str, values: dict | None = None) -> str:
    """生成依赖安装命令；返回空串表示无需安装。"""
    values = values or {}
    d = get_provider_def(pid)
    if pid == "hindsight" and d.deps_dynamic:
        pkgs = ("hindsight-all",) if values.get("mode") == "local" else ("hindsight-client",)
    else:
        pkgs = d.deps_pip
    if not pkgs:
        return ""
    return (
        "{ " + PIP_RESOLVE +
        " " + " ".join(f'"$PIP" install --quiet "{pkg}" &&' for pkg in pkgs[:-1]) +
        f' "$PIP" install --quiet "{pkgs[-1]}" && echo "deps installed: {" ".join(pkgs)}"; }}'
    )


def submit_install_job(pid: str, values: dict | None = None) -> int:
    from app.hermes import installer

    command = deps_command(pid, values) or get_provider_def(pid).job_command
    if not command:
        raise MemoryError(f"{get_provider_def(pid).label} 无需额外安装任务")
    return installer.submit(f"memory_{pid}", command)


def job_active_for(pid: str) -> bool:
    from app.hermes import installer

    job = installer.active_job()
    return bool(job and job["kind"] == f"memory_{pid}")
