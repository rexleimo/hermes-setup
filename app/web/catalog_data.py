"""Hermes 配置项全景（基于官方 cli-config.yaml.example 与文档整理，2026-09）。

integration 取值：
  integrated   — 本平台已集成
  phase2       — 建议下一批集成（高价值、低风险）
  evaluate     — 值得评估（有价值但需权衡）
  by-hand      — 建议继续手改（高度个性化 / 低频）
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigArea:
    key: str                # config.yaml 顶层键
    name: str
    summary: str
    items: tuple[str, ...]  # 代表性子键
    integration: str
    note: str = ""


AREAS: tuple[ConfigArea, ...] = (
    ConfigArea(
        "model", "主模型（model.*）",
        "默认模型、推理供应商、协议模式、上下文长度、流式开关",
        ("default", "provider", "api_mode", "base_url", "context_length", "streaming",
         "default_headers"),
        "integrated", "供应商管理「设为主模型」直接写入。",
    ),
    ConfigArea(
        "providers", "自定义供应商（providers.<id>）",
        "命名供应商端点：base_url / api_mode / key_env / extra_headers / 超时",
        ("<id>.base_url", "<id>.api_mode", "<id>.key_env", "<id>.key_cmd",
         "<id>.extra_headers", "<id>.request_timeout_seconds", "<id>.models.<m>.timeout_seconds"),
        "integrated", "含命令铸币凭证 key_cmd（短期令牌）的编辑已在二期评估中。",
    ),
    ConfigArea(
        "delegation.fallback_providers", "备选模型链",
        "主模型失败后的降级链，每项 {provider, model, base_url?, key_env?}",
        ("fallback_providers[].provider", "fallback_providers[].model"),
        "integrated", "「链路」页可视化排序编辑。",
    ),
    ConfigArea(
        "model_aliases", "模型别名",
        "为任意 端点+模型 组合起短别名，/model 补全与 resolve_alias 使用",
        ("<alias>.model", "<alias>.provider", "<alias>.base_url", "<alias>.key_env"),
        "integrated", "供应商详情页「发布别名」。",
    ),
    ConfigArea(
        "platforms.* / platform_toolsets", "消息渠道与工具集",
        "渠道启用、平台级参数（extra）、每渠道工具白名单",
        ("platforms.<name>.enabled", "platforms.<name>.extra", "platform_toolsets.<name>"),
        "integrated", "渠道模块；群细粒度规则（group_rules）当前以 JSON 高级编辑。",
    ),
    ConfigArea(
        "terminal", "执行后端（终端沙箱）",
        "七种后端：local / docker / ssh / modal / daytona / vercel_sandbox / singularity",
        ("backend", "timeout", "docker_image", "docker_env", "docker_volumes",
         "container_cpu", "container_memory", "home_mode", "env_passthrough"),
        "phase2", "安全边界核心；建议做成「执行环境」页 + 预设模板（local/docker 起步）。",
    ),
    ConfigArea(
        "mcp_servers", "MCP 服务器",
        "stdio / http(sse) 两类外部工具源；env/headers 支持 ${VAR} 密钥引用",
        ("<id>.command", "<id>.args", "<id>.env", "<id>.url", "<id>.headers",
         "<id>.enabled", "<id>.timeout", "<id>.trust", "<id>.auth"),
        "integrated",
        "「MCP 服务」页：CRUD + 启停 + 官方热门目录（optional-mcps）一键添加；"
        "密钥只写 .env，配置中以 ${VAR} 占位符引用（官方 secret-scope 语义）。",
    ),
    ConfigArea(
        "skills", "技能生态",
        "技能启停名单、外部目录挂载、项目技能信任、安全开关",
        ("skills.disabled", "skills.external_dirs", "skills.trusted_project_dirs",
         "skills.template_vars", "skills.inline_shell", "skills.guard_agent_created"),
        "integrated",
        "「技能管理」页：skills/ 目录可视化（frontmatter 解析 + 启停/删除）+ "
        "官方热门技能库（optional-skills）一键安装 + skills 域设置表单；"
        "hermes-agent 必备技能拒绝禁用（与官方 ESSENTIAL_SKILLS 对齐）。",
    ),
    ConfigArea(
        "plugins.enabled", "插件白名单",
        "~/.hermes/plugins/ 目录插件 + 官方策展目录（plugin-catalog）",
        ("plugins.enabled", "plugins.hook_callback_timeout"),
        "integrated",
        "「插件与 Hook」页：启停 = 增删白名单（官方信任模型，默认禁用）；"
        "策展目录安装走官方 CLI 后台任务（sha pin / 黑名单交给官方校验）。",
    ),
    ConfigArea(
        "hooks / hooks_auto_accept", "Shell Hooks",
        "config.yaml 声明式钩子：pre_tool_call 拦截、post_tool_call 后处理、pre_llm_call 注入上下文",
        ("hooks.<event>[].command", "hooks.<event>[].matcher", "hooks.<event>[].timeout",
         "hooks.<event>[].fail_closed", "hooks_auto_accept"),
        "integrated",
        "「插件与 Hook」页：CRUD 校验对齐官方（matcher 仅工具事件、fail_closed 仅 "
        "pre_tool_call、timeout ≤ 300）；信任白名单可撤销；gateway hooks 与 "
        "outbound webhooks 只读盘点。详见 docs/PLUGINS_AND_HOOKS.md。",
    ),
    ConfigArea(
        "cron / cron 目录", "定时任务",
        "~/.hermes/cron/ 下的计划任务 + catch_up_missed 容错",
        ("cron.catch_up_missed", "cronjob 工具创建的任务"),
        "phase2", "读取 cron/ 目录做只读列表 + 启停（经 cronjob 工具），二期实现。",
    ),
    ConfigArea(
        "auxiliary", "辅助模型",
        "vision / title_generation / compression / approval 等小任务模型路由",
        ("auxiliary.vision", "auxiliary.compression.model", "auxiliary.approval"),
        "phase2", "可直接复用供应商选择器；对成本优化价值大。",
    ),
    ConfigArea(
        "smart_model_routing", "智能省费路由",
        "简单问题自动切廉价模型（字数/关键词阈值）",
        ("enabled", "max_simple_chars", "cheap_model.*"),
        "evaluate", "规则细节多，建议先做「开关 + 阈值」三控件。",
    ),
    ConfigArea(
        "memory + memory.provider", "记忆系统（内置调优 + 外置 Provider）",
        "MEMORY.md/USER.md 容量与审批；9 家外置记忆方案（含社区 agentmemory）一键切换",
        ("memory_enabled", "memory_char_limit", "user_char_limit", "write_approval",
         "provider", "mem0.json", "honcho.json", "supermemory.json",
         "hindsight/config.json", "mcp_servers.agentmemory"),
        "integrated",
        "「记忆系统」页：内置容量预设（默认 2200/1375 → 最高 16000/8000）、"
        "外置方案表单化自动写入；agentmemory 支持 MCP 与 Provider 插件两种形态。",
    ),
    ConfigArea(
        "compression", "上下文压缩",
        "阈值、保护窗口、卫生检查等 20+ 细粒度键",
        ("enabled", "threshold", "target_ratio", "protect_last_n", "tail_mode"),
        "evaluate", "键多而敏感，建议只暴露 4-5 个常用键做「性能调优」卡片。",
    ),
    ConfigArea(
        "gateway", "网关运行参数",
        "SIGTERM 优雅退出宽限、代理环境信任开关",
        ("signal_interrupt_grace_timeout", "trust_env"),
        "evaluate", "与 systemd 等外部管理器预算相关，谨慎暴露。",
    ),
    ConfigArea(
        "updates", "更新策略",
        "升级前备份、备份保留数、本地改动处理",
        ("pre_update_backup", "backup_keep", "non_interactive_local_changes"),
        "phase2", "与服务管理「一键更新」强相关，随更新功能一起暴露。",
    ),
    ConfigArea(
        "database / runtime", "运行时与数据库",
        "SQLite WAL、句柄上限",
        ("database.journal_mode", "runtime.nofile_soft_limit"),
        "by-hand", "改动影响数据安全，保持手改。",
    ),
    ConfigArea(
        "display / streaming", "显示与流式渲染",
        "横幅、工具过程显示级别、token 渲染",
        ("display.compact", "display.tool_progress", "streaming.enabled"),
        "by-hand", "纯观感偏好，价值低。",
    ),
    ConfigArea(
        "SOUL.md / 个性 + agent.coding_instructions + skills/",
        "工程规范与人格（Engineering Guardrails）",
        "SOUL.md 托管块注入工作区布局与工程铁律；三个工程技能（project-init / "
        "requirement-digest / file-placement）；coding_instructions 编码硬约束",
        ("SOUL.md 托管块", "agent.coding_instructions", "skills/project-init",
         "skills/requirement-digest", "skills/file-placement"),
        "integrated",
        "「工程规范」页：参考 AIOS 的项目制与文件归位纪律，幂等注入可一键卸载，"
        "用户自有 SOUL.md 内容不受影响。",
    ),
    ConfigArea(
        " personalities", "多套人格（personalities）",
        "agent.personalities 定义多套可切换人格",
        ("agent.personalities"),
        "evaluate", "工程规范已覆盖单一身份的工程纪律；多套人格按需排期。",
    ),
    ConfigArea(
        "tool_loop_guardrails / agent", "Agent 行为护栏",
        "循环警告与硬停、最大轮数、预算",
        ("tool_loop_guardrails.hard_stop_enabled", "agent.max_turns", "agent.run_budget_seconds"),
        "evaluate", "成本与安全相关，适合「护栏」卡片；键少。",
    ),
    ConfigArea(
        "stt / tts / browser / image_gen", "语音与多媒体",
        "语音识别、合成、浏览器与图像生成供应商",
        ("stt.provider", "tts.provider", "browser.extension_control"),
        "evaluate", "多模态扩展面，按用户反馈排期。",
    ),
    ConfigArea(
        "privacy / secrets", "隐私与密钥管理",
        "PII 脱敏、启动期密钥注入命令",
        ("privacy.redact_pii", "secrets.command"),
        "evaluate", "企业部署强需求；secrets.command 涉及凭证链，需安全评审。",
    ),
)

INTEGRATION_LABELS = {
    "integrated": ("已集成", "ok"),
    "phase2": ("建议二期", "info"),
    "evaluate": ("待评估", "warn"),
    "by-hand": ("建议手改", "muted"),
}
