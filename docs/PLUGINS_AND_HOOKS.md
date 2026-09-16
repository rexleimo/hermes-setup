# Hermes 插件与 Hook 机制调研（v0.8.0 随附）

> 结论先行：**Hermes Agent 有一套完整的插件与 Hook 体系**，共四套 Hook + 一个插件系统。
> 本平台 v0.8.0 起在「插件与 Hook」页提供管理界面；本文是机制调研的沉淀，
> 依据为本机安装的 hermes-agent 源码（`~/.hermes/hermes-agent/`）与官方文档站
> （`website/docs/user-guide/features/{hooks,plugins,mcp,skills}.md`）。

## 四套 Hook 一览

| 体系 | 注册方式 | 运行范围 | 本平台支持 |
|---|---|---|---|
| **Shell hooks** | `config.yaml` 的 `hooks.<event>[]` 指向脚本 | CLI + Gateway + Desktop/TUI/dashboard | ✅ 完整 CRUD |
| **Plugin hooks** | Python 插件内 `ctx.register_hook()` | CLI + Gateway | ✅ 随插件白名单启停 |
| **Gateway hooks** | `~/.hermes/hooks/<name>/{HOOK.yaml, handler.py}` | 仅 Gateway | 只读盘点 |
| **Outbound webhooks** | `config.yaml` 的 `hooks.outbound[]` | CLI + Gateway | 只读盘点 |

Hook 回调出错会被隔离并记日志，不会搞崩 agent。Hook 不全是被动的：
`pre_tool_call` 可拦截工具调用，transform 类可改写内容，shell hook 以 exit code 2 表示拦截
（与 Claude Code / Cursor 兼容）。

## Shell hooks（最易用，无需写 Python）

配置形态（写入 `config.yaml`）：

```yaml
hooks:
  pre_tool_call:
    - matcher: "terminal"          # 可选，仅 pre/post_tool_call；按工具名正则过滤
      command: "/opt/guard.sh"     # 必填；shlex.split 执行，shell=False
      timeout: 60                  # 默认 60，上限 300
      fail_closed: true            # 仅 pre_tool_call；hook 失败视为拦截
hooks_auto_accept: false           # true = 新 hook 跳过首次确认
```

- 事件全集 = 官方 `VALID_HOOKS`（37 个，`hermes_cli/plugins.py`）：常用的有
  `pre_tool_call / post_tool_call`（拦截/后处理）、`pre_llm_call`（注入上下文）、
  `subagent_stop`、`on_session_*`（生命周期）、`pre_approval_request`（审批观察）等；
- stdin 收 JSON payload（`tool_name` / `tool_input` / `cwd` / `profile` …），
  stdout 可回 `{"action": "block"|"modify", ...}` 或 `{"context": ...}`；
- **信任模型**：每个 `(event, command)` 首次触发需确认（TTY 提示或
  `hooks_auto_accept`），同意记录落盘 `~/.hermes/agent-hooks/shell-hooks-allowlist.json`，
  可用 `hermes hooks revoke` 或本平台页面撤销。

## Python 插件系统

- 目录：`~/.hermes/plugins/<name>/`，`plugin.yaml`（name/version/description/hooks…）
  + `__init__.py` 暴露 `register(ctx)`；
- ctx API：`register_tool / register_hook / register_command / register_cli_command /
  inject_message / register_skill / call_mcp()`（插件可反向调用 MCP 工具）；
- **默认禁用**：必须显式加入 `config.yaml` 的 `plugins.enabled` 白名单才会加载
  —— 防止第三方代码未经同意执行。本平台的「启用/禁用」即增删白名单项；
- `plugins.hook_callback_timeout`（默认 30s）限制插件 hook 回调耗时；
- 官方策展目录：`agent_repo/plugin-catalog/*.yaml`，每条含 `repo + 40 位 sha 锁定 +
  capabilities（provides_tools / provides_hooks / requires_env）`，
  `removed.yaml` 为安全黑名单。安装走官方 CLI（`hermes plugins install`，
  sha 校验与黑名单拦截全部由官方实现），本平台以后台任务方式调用它；
- Gateway hooks（`~/.hermes/hooks/`）与 outbound webhooks（签名事件推送到外部 HTTP）
  本期只读展示，编辑需求出现时再做。

## 顺带核实的 Skill / MCP 事实（v0.8.0 另两页的依据）

- **Skill**：`~/.hermes/skills/<slug>/SKILL.md`（agentskills.io 兼容 frontmatter）；
  三层优先级 project → local → external_dirs，项目技能须经 `skills.trusted_project_dirs`
  信任后才加载；启停 = `skills.disabled` 名单，`hermes-agent` 属官方
  `ESSENTIAL_SKILLS` 永不禁用；官方热门库在源码 `optional-skills/`（24 类），
  `hermes skills install` 的本质是复制目录。
- **MCP**：`config.yaml` 顶层 `mcp_servers.<name>`，stdio（command/args/env）与
  http（url/headers，`transport: sse` 可选，`auth: oauth` 支持 OAuth 2.1 + PKCE）；
  `enabled / timeout / trust(untrusted = 写操作走审批)` 等通用键；
  env/headers 中的 `${VAR}` / `${env:VAR}` 占位符在连接时解析（先 profile secret
  scope，回退进程环境）——官方明确建议密钥放 `~/.hermes/.env`，与本平台
  「secrets 只进 .env」的写入契约天然一致；官方热门目录在源码 `optional-mcps/`
  （64 个 Nous 审核条目，含 manifest 描述 / 传输 / 认证方式 / post_install 提示）。

## 本平台写入的键（对齐官方 schema）

| 键 | 管理动作 |
|---|---|
| `plugins.enabled` | 插件启用 = 追加、禁用 = 移除 |
| `plugins.hook_callback_timeout` | 表单数值（默认 30，不落键） |
| `hooks.<event>[]` | shell hooks 新增/覆盖/删除（校验规则同官方） |
| `hooks_auto_accept` | 开关（默认 false，不落键） |
| `agent-hooks/shell-hooks-allowlist.json` | 只读展示 + 撤销条目 |

其余键（gateway hooks 目录、outbound webhooks、profile 体系）保持官方编辑路径。
