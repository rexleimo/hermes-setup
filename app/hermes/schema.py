"""Hermes 配置的 UI 描述符：协议、预设供应商、消息渠道字段。

依据 Hermes Agent 官方文档与 cli-config.yaml.example 整理（2026-09）：

- model.api_mode 允许值：chat_completions / codex_responses / anthropic_messages；
  Bedrock、Gemini Native、Vertex 走原生 provider 适配器（provider id 而非 api_mode）。
- model.provider 内置 id：auto, openrouter, nous, nous-api, anthropic, openai-codex,
  copilot, gemini, zai, kimi-coding, minimax, minimax-cn, huggingface, nvidia,
  xiaomi, arcee, ollama-cloud, deepinfra, kilocode, ai-gateway, azure-foundry,
  lmstudio, custom（别名 ollama/vllm/llamacpp → custom）。
- 渠道（平台）配置混合存在于 config.yaml（platforms.<name>.enabled/extra）与
  ~/.hermes/.env（令牌类敏感键）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 基础描述符
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FieldDef:
    name: str
    label: str
    kind: str = "text"        # text | password | select | textarea | bool | int
    required: bool = False
    options: tuple[str, ...] = ()
    default: str = ""
    help: str = ""
    placeholder: str = ""
    verified: bool = True     # False = 字段名以官方文档为准（占位提示）
    visible_if_field: str = ""    # 条件显示：依赖的字段名
    visible_if_value: str = ""    # 条件显示：依赖字段等于该值时才显示


@dataclass(frozen=True)
class ProtocolDef:
    id: str                   # 平台内部协议 id
    label: str
    api_mode: str | None      # 写入 providers.<id>.api_mode；None = 原生 provider
    provider_id: str | None   # 原生 provider（bedrock / gemini）时使用
    needs_base_url: bool = True
    env_key_hint: str = ""    # 推荐 .env 键名
    list_models: str = "openai"   # openai | anthropic | gemini | manual
    note: str = ""


PROTOCOLS: dict[str, ProtocolDef] = {
    "openai_chat": ProtocolDef(
        id="openai_chat", label="OpenAI Chat 兼容", api_mode="chat_completions",
        provider_id=None, env_key_hint="auto",
        list_models="openai",
        note="绝大多数聚合网关与自建推理服务（vLLM / Ollama / LM Studio）使用此协议。",
    ),
    "openai_responses": ProtocolDef(
        id="openai_responses", label="OpenAI Responses", api_mode="codex_responses",
        provider_id=None, env_key_hint="auto",
        list_models="openai",
        note="Hermes 内部称 codex_responses，适用于 Responses API 原生端点。",
    ),
    "anthropic_messages": ProtocolDef(
        id="anthropic_messages", label="Anthropic Messages", api_mode="anthropic_messages",
        provider_id=None, env_key_hint="ANTHROPIC_API_KEY",
        list_models="anthropic",
        note="Anthropic 原生 Messages 协议或兼容代理。",
    ),
    "aws_bedrock": ProtocolDef(
        id="aws_bedrock", label="AWS Bedrock", api_mode=None,
        provider_id="bedrock", needs_base_url=False,
        env_key_hint="AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY",
        list_models="manual",
        note="凭证由 AWS_* 环境变量提供（在 .env 中配置）；模型需手动录入。",
    ),
    "google_gemini": ProtocolDef(
        id="google_gemini", label="Google Gemini (AI Studio)", api_mode=None,
        provider_id="gemini", needs_base_url=False,
        env_key_hint="GOOGLE_API_KEY",
        list_models="gemini",
        note="Google AI Studio 直连；凭证写入 GOOGLE_API_KEY。",
    ),
}


@dataclass(frozen=True)
class PresetDef:
    id: str                    # Hermes provider id
    label: str
    protocol: str              # PROTOCOLS key
    base_url: str = ""
    env_key: str = ""          # 官方约定的 .env 键名
    docs: str = ""


PRESETS: dict[str, PresetDef] = {
    "openrouter": PresetDef("openrouter", "OpenRouter", "openai_chat",
                            "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "anthropic": PresetDef("anthropic", "Anthropic 官方", "anthropic_messages",
                           "https://api.anthropic.com", "ANTHROPIC_API_KEY"),
    "gemini": PresetDef("gemini", "Google Gemini", "google_gemini",
                        "", "GOOGLE_API_KEY"),
    "deepseek": PresetDef("deepseek", "DeepSeek", "openai_chat",
                          "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "zai": PresetDef("zai", "Z.ai / 智谱 GLM", "openai_chat",
                     "https://api.z.ai/api/paas/v4", "GLM_API_KEY"),
    "kimi-coding": PresetDef("kimi-coding", "Kimi / Moonshot", "openai_chat",
                             "https://api.moonshot.cn/v1", "KIMI_API_KEY"),
    "minimax": PresetDef("minimax", "MiniMax", "openai_chat",
                         "https://api.minimax.io/v1", "MINIMAX_API_KEY"),
    "minimax-cn": PresetDef("minimax-cn", "MiniMax 国内", "openai_chat",
                            "https://api.minimaxi.com/v1", "MINIMAX_CN_API_KEY"),
    "deepinfra": PresetDef("deepinfra", "DeepInfra", "openai_chat",
                           "https://api.deepinfra.com/v1/openai", "DEEPINFRA_API_KEY"),
    "nvidia": PresetDef("nvidia", "NVIDIA NIM", "openai_chat",
                        "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY"),
    "lmstudio": PresetDef("lmstudio", "LM Studio（本地）", "openai_chat",
                          "http://127.0.0.1:1234/v1", "LM_API_KEY"),
    "ollama-cloud": PresetDef("ollama-cloud", "Ollama Cloud", "openai_chat",
                              "https://ollama.com/v1", "OLLAMA_API_KEY"),
    "bedrock": PresetDef("bedrock", "AWS Bedrock", "aws_bedrock",
                         "", "AWS_ACCESS_KEY_ID"),
}


# ---------------------------------------------------------------------------
# 消息渠道（platforms）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PlatformDef:
    id: str                    # platforms.<id> / 文档名
    label: str
    emoji: str = "💬"
    doc: str = ""
    env_fields: tuple[FieldDef, ...] = ()
    extra_fields: tuple[FieldDef, ...] = ()
    config_keys: tuple[FieldDef, ...] = ()   # platforms.<id> 下的直接键（enabled 之外）
    verified: bool = True
    note: str = ""
    config_scope: str = "platforms"   # config_keys 写入位置：platforms.<id> | 顶层 <id>


COMMON_POLICIES = ("open", "allowlist", "disabled")

PLATFORMS: dict[str, PlatformDef] = {
    "feishu": PlatformDef(
        id="feishu", label="飞书 / Lark", emoji="🕊️",
        doc="https://hermes-agent.nousresearch.com/docs/zh-Hans/user-guide/messaging/feishu",
        env_fields=(
            FieldDef("FEISHU_APP_ID", "App ID", required=True,
                     help="飞书开放平台企业自建应用 App ID"),
            FieldDef("FEISHU_APP_SECRET", "App Secret", kind="password", required=True),
            FieldDef("FEISHU_DOMAIN", "域名", kind="select",
                     options=("feishu", "lark"), default="feishu"),
            FieldDef("FEISHU_CONNECTION_MODE", "连接模式", kind="select",
                     options=("websocket", "webhook"), default="websocket"),
            FieldDef("FEISHU_ALLOWED_USERS", "用户白名单（open_id，逗号分隔）", kind="textarea"),
            FieldDef("FEISHU_GROUP_POLICY", "群聊策略", kind="select",
                     options=("open", "allowlist", "disabled"), default="allowlist"),
            FieldDef("FEISHU_REQUIRE_MENTION", "需要 @ 机器人", kind="bool", default="true"),
            FieldDef("FEISHU_ENCRYPT_KEY", "Encrypt Key", kind="password"),
            FieldDef("FEISHU_VERIFICATION_TOKEN", "Verification Token", kind="password"),
        ),
        extra_fields=(
            FieldDef("admins", "管理员（open_id 列表）", kind="textarea"),
            FieldDef("ws_reconnect_interval", "WS 重连间隔（秒）", kind="int"),
            FieldDef("ws_ping_interval", "WS 心跳间隔（秒）", kind="int"),
        ),
        note="字段已逐一对照官方文档核对；群细粒度规则（group_rules）在「高级编辑」中以 JSON 配置。",
    ),
    "telegram": PlatformDef(
        id="telegram", label="Telegram", emoji="✈️",
        doc="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram",
        env_fields=(
            FieldDef("TELEGRAM_BOT_TOKEN", "Bot Token", kind="password", required=True,
                     help="@BotFather 创建机器人获得"),
            FieldDef("TELEGRAM_ALLOWED_USERS", "用户白名单（用户名或 ID，逗号分隔）", kind="textarea"),
            FieldDef("TELEGRAM_ALLOWED_CHATS", "群组白名单（chat ID，逗号分隔）", kind="textarea"),
        ),
        extra_fields=(
            FieldDef("disable_link_previews", "禁用链接预览", kind="bool"),
            FieldDef("rich_messages", "富文本消息（Bot API）", kind="bool"),
            FieldDef("status_indicator", "在线状态指示", kind="bool"),
        ),
        config_keys=(
            FieldDef("reply_to_mode", "回复模式", kind="select", options=("off", "first", "all")),
            FieldDef("guest_mode", "访客模式", kind="bool"),
        ),
    ),
    "discord": PlatformDef(
        id="discord", label="Discord", emoji="🎮",
        doc="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/discord",
        env_fields=(
            FieldDef("DISCORD_BOT_TOKEN", "Bot Token", kind="password", required=True,
                     help="Discord Developer Portal 创建应用获得"),
            FieldDef("DISCORD_ALLOWED_USERS", "用户白名单", kind="textarea"),
            FieldDef("DISCORD_ALLOWED_CHANNELS", "频道白名单", kind="textarea"),
        ),
        extra_fields=(),
        config_keys=(
            FieldDef("require_mention", "需要 @（写入顶层 discord.*）", kind="bool", default="true"),
            FieldDef("auto_thread", "自动建线程（顶层 discord.*）", kind="bool", default="true"),
        ),
        config_scope="top",
    ),
    "slack": PlatformDef(
        id="slack", label="Slack", emoji="#️⃣",
        doc="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/slack",
        env_fields=(
            FieldDef("SLACK_BOT_TOKEN", "Bot Token (xoxb-)", kind="password", required=True),
            FieldDef("SLACK_APP_TOKEN", "App Token (xapp-，Socket Mode)", kind="password"),
            FieldDef("SLACK_ALLOWED_USERS", "用户白名单", kind="textarea"),
            FieldDef("SLACK_ALLOWED_CHANNELS", "频道白名单", kind="textarea"),
        ),
        extra_fields=(
            FieldDef("unfurl_links", "展开链接预览卡片", kind="bool"),
            FieldDef("unfurl_media", "展开媒体预览卡片", kind="bool"),
            FieldDef("native_task_cards", "任务卡片", kind="bool"),
        ),
    ),
    "qqbot": PlatformDef(
        id="qqbot", label="QQ 机器人", emoji="🐧",
        doc="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/qqbot",
        env_fields=(
            FieldDef("QQ_ALLOWED_USERS", "私聊白名单（OpenID，逗号分隔）", kind="textarea"),
            FieldDef("QQ_GROUP_ALLOWED_USERS", "群白名单（OpenID，逗号分隔）", kind="textarea"),
        ),
        extra_fields=(
            FieldDef("app_id", "App ID", required=True,
                     help="QQ 开放平台机器人 App ID"),
            FieldDef("client_secret", "App Secret", kind="password", required=True),
            FieldDef("markdown_support", "启用 QQ Markdown（msg_type 2）", kind="bool"),
            FieldDef("dm_policy", "私聊策略（extra 方式）", kind="select",
                     options=("open", "allowlist", "disabled"), default="open"),
            FieldDef("allow_from", "私聊白名单（OpenID 列表）", kind="textarea"),
            FieldDef("group_policy", "群聊策略（extra 方式）", kind="select",
                     options=("open", "allowlist", "disabled"), default="open"),
            FieldDef("group_allow_from", "群白名单（OpenID 列表）", kind="textarea"),
        ),
        note="凭证写入 platforms.qqbot.extra（app_id / client_secret）—— Gateway 的连接判定从此处读取；"
             ".env 的 QQ_APP_ID / QQ_CLIENT_SECRET 亦被适配器支持。",
    ),
    "wecom": PlatformDef(
        id="wecom", label="企业微信", emoji="💼",
        doc="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/wecom",
        env_fields=(
            FieldDef("WECOM_BOT_ID", "Bot ID（env 方式）", required=True),
            FieldDef("WECOM_SECRET", "Secret（env 方式）", kind="password", required=True),
        ),
        extra_fields=(
            FieldDef("bot_id", "Bot ID（extra 方式）"),
            FieldDef("secret", "Secret（extra 方式）", kind="password"),
            FieldDef("websocket_url", "WebSocket 网关地址",
                     default="wss://openws.work.weixin.qq.com"),
            FieldDef("dm_policy", "私聊策略", kind="select",
                     options=("open", "allowlist", "disabled", "pairing"), default="open"),
            FieldDef("group_policy", "群聊策略", kind="select",
                     options=("open", "allowlist", "disabled"), default="open"),
            FieldDef("allow_from", "私聊白名单（ID 列表）", kind="textarea"),
            FieldDef("group_allow_from", "群白名单（ID 列表）", kind="textarea"),
        ),
        note="凭证二选一：.env 的 WECOM_BOT_ID/WECOM_SECRET 或 extra 的 bot_id/secret；"
             "群细粒度白名单在高级编辑中配置。",
    ),
    "weixin": PlatformDef(
        id="weixin", label="微信（iLink Bot）", emoji="💚",
        doc="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/weixin",
        env_fields=(
            FieldDef("WEIXIN_ALLOWED_USERS", "用户白名单", kind="textarea"),
        ),
        extra_fields=(
            FieldDef("account_id", "iLink Bot 账号 ID", required=True,
                     help="服务器上执行 `hermes weixin` 扫码登录后获得"),
        ),
        config_keys=(
            FieldDef("dm_policy", "私聊策略", kind="select", options=COMMON_POLICIES, default="open"),
            FieldDef("group_policy", "群聊策略", kind="select", options=COMMON_POLICIES,
                     default="disabled"),
        ),
    ),
    "dingtalk": PlatformDef(
        id="dingtalk", label="钉钉", emoji="🔗",
        doc="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/dingtalk",
        env_fields=(
            FieldDef("DINGTALK_CLIENT_ID", "Client ID", required=True),
            FieldDef("DINGTALK_CLIENT_SECRET", "Client Secret", kind="password", required=True),
            FieldDef("DINGTALK_ALLOWED_USERS", "用户白名单", kind="textarea"),
        ),
    ),
    "email": PlatformDef(
        id="email", label="Email（IMAP/SMTP）", emoji="📧",
        doc="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/email",
        env_fields=(
            FieldDef("EMAIL_ADDRESS", "邮箱地址", required=True),
            FieldDef("EMAIL_PASSWORD", "邮箱密码 / 授权码", kind="password", required=True),
            FieldDef("EMAIL_IMAP_HOST", "IMAP 服务器", required=True),
            FieldDef("EMAIL_IMAP_PORT", "IMAP 端口", kind="int", default="993"),
            FieldDef("EMAIL_SMTP_HOST", "SMTP 服务器", required=True),
            FieldDef("EMAIL_SMTP_PORT", "SMTP 端口", kind="int", default="465"),
        ),
    ),
    "whatsapp": PlatformDef(
        id="whatsapp", label="WhatsApp", emoji="🌍",
        doc="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/whatsapp",
        env_fields=(
            FieldDef("WHATSAPP_ENABLED", "启用 WhatsApp 桥接", kind="bool", default="true"),
            FieldDef("WHATSAPP_ALLOWED_USERS", "用户白名单", kind="textarea"),
        ),
        note="基于 Baileys 非官方桥接，需要 Node.js；首次使用在服务器上扫码配对。",
    ),
    "signal": PlatformDef(
        id="signal", label="Signal", emoji="🔒",
        doc="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/signal",
        env_fields=(
            FieldDef("SIGNAL_ALLOWED_USERS", "用户白名单", kind="textarea"),
        ),
        extra_fields=(
            FieldDef("http_url", "signal-cli REST 服务地址", required=True,
                     default="http://127.0.0.1:8080"),
            FieldDef("account", "Signal 账号（手机号）", required=True),
        ),
        note="需要自行运行 signal-cli REST API 服务。",
    ),
    "matrix": PlatformDef(
        id="matrix", label="Matrix", emoji="🟠",
        doc="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/matrix",
        env_fields=(
            FieldDef("MATRIX_HOMESERVER", "Homeserver 地址", required=True),
            FieldDef("MATRIX_USER_ID", "用户 ID", required=True, placeholder="@bot:example.org"),
            FieldDef("MATRIX_ACCESS_TOKEN", "Access Token", kind="password", required=True),
            FieldDef("MATRIX_PASSWORD", "密码（可选，与 Token 二选一）", kind="password"),
        ),
        note="官方文档标注 Matrix 渠道仅支持 Linux。",
    ),
    "webhook": PlatformDef(
        id="webhook", label="Webhook", emoji="🪝",
        doc="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/webhooks",
        extra_fields=(
            FieldDef("script_timeout_seconds", "脚本超时（秒）", kind="int", default="30"),
        ),
        note="脚本须位于当前 profile 的 scripts 目录，stdin 接收 webhook JSON。",
    ),
    "api_server": PlatformDef(
        id="api_server", label="API Server", emoji="🛰️",
        doc="https://hermes-agent.nousresearch.com/docs/user-guide/messaging/",
        extra_fields=(
            FieldDef("key", "访问密钥（API Key）", kind="password", required=True,
                     help="至少 16 个字符；自建前端（含移动端）调用时携带"),
            FieldDef("host", "监听地址", default="127.0.0.1"),
        ),
        note="开放 HTTP API，供自建前端（含移动端）调用。密钥写入 platforms.api_server.extra.key"
             "（Gateway 连接判定从此处读取，长度需 ≥16）。",
    ),
}

PLATFORM_TOOLSET_KEYS: dict[str, str] = {
    "cli": "CLI 终端", "telegram": "Telegram", "discord": "Discord", "whatsapp": "WhatsApp",
    "slack": "Slack", "signal": "Signal", "homeassistant": "Home Assistant",
    "qqbot": "QQ", "yuanbao": "腾讯元宝", "teams": "Teams", "google_chat": "Google Chat",
}

TOOLSET_CHOICES: tuple[str, ...] = (
    "web", "search", "terminal", "file", "browser", "vision", "image_gen",
    "skills", "skills_hub", "todo", "tts", "cronjob", "memory", "session_search", "messaging",
)
TOOLSET_PRESETS: tuple[str, ...] = (
    "hermes-cli", "hermes-telegram", "hermes-discord", "hermes-whatsapp", "hermes-slack",
    "hermes-signal", "hermes-homeassistant", "hermes-qqbot", "hermes-yuanbao",
    "hermes-teams", "hermes-google_chat",
)


def platform_enabled_fields(p: PlatformDef) -> tuple[FieldDef, ...]:
    return p.config_keys + tuple(
        FieldDef(f"extra.{f.name}", f.label, f.kind, f.required, f.options, f.default,
                 f.help, f.verified)
        for f in p.extra_fields
    )


__all__ = [
    "FieldDef", "ProtocolDef", "PresetDef", "PlatformDef",
    "PROTOCOLS", "PRESETS", "PLATFORMS", "PLATFORM_TOOLSET_KEYS",
    "TOOLSET_CHOICES", "TOOLSET_PRESETS", "platform_enabled_fields",
]
