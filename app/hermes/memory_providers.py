"""外置记忆方案（provider）定义目录。

从 memory_service.py 拆出：本文件只放「定义」——方案描述符、各方案的
安装命令/依赖、表单字段；行为逻辑（读状态、写配置、提交任务）留在
memory_service.py。新增 provider = 只改这里。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.hermes.schema import FieldDef


class MemoryError(Exception):
    """记忆域错误（未知 provider / 参数缺失等）。"""
    pass

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

