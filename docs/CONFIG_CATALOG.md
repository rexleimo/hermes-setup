# Hermes Agent 配置项深挖清单

> 目的：三大核心模块（服务管理 / 供应商 / 渠道）之外，盘点 Hermes Agent 还有哪些配置域，
> 评估哪些适合进一步集成到平台。供团队评审排期。
> 依据：官方 `cli-config.yaml.example`（约 2100 行，2026-09 main 分支）、官方文档站与社区实践。
> 本清单与控制台「配置项全景」页共用同一份数据源（`app/web/catalog_data.py`）。

## 总览

| 分类 | 数量 | 说明 |
|---|---|---|
| ✅ 已集成 | 6 | 主模型、providers、备选链、别名、渠道、记忆系统 |
| 🔵 建议二期 | 5 | 价值高、CRUD 形态清晰 |
| 🟡 待评估 | 7 | 有价值但需权衡（键多/敏感/低频） |
| ⚪ 建议手改 | 2 | 数据安全相关，保持官方编辑路径 |

---

## ✅ 已集成（本期交付）

| 配置域 | 键 | 集成方式 |
|---|---|---|
| `model.*` | provider / default / api_mode / base_url / context_length | 供应商「设为主模型」 |
| `providers.<id>` | base_url / api_mode / key_env / extra_headers | 供应商 CRUD（key_cmd 见二期） |
| `delegation.fallback_providers` | [{provider, model, base_url?, key_env?}] | 「链路」页排序编辑 |
| `model_aliases` | <alias>.{model, provider, base_url?, key_env?} | 供应商页「发布别名」 |
| `platforms.*` / `platform_toolsets` | enabled / extra / 工具集覆盖 | 渠道模块 |
| `memory.*` + `memory.provider` | 内置容量/审批 + 外置方案切换 | 「记忆系统」页：容量预设（2200/1375 → 16000/8000）、`context_file_max_chars` 上限、9 家外置方案（AgentMemory / Mem0 / Supermemory / OpenViking / Hindsight / Holographic / RetainDB / ByteRover / Honcho）表单化自动写入各 JSON 配置与 .env；AgentMemory 支持零代码 MCP（`mcp_servers.agentmemory`）与 Provider 插件（自动下载安装）两种形态 |

> **背景**：内置记忆默认仅 2,200/1,375 字符（≈1,300 token），大项目极易写满报错——
> 这是「软件能跑但记忆没配好」的根因。平台提供容量预设一键调高，并引导切换外置方案。

---

## 🔵 建议二期（高价值、低风险、形态清晰）

### 1. `terminal.*` — 执行后端（强烈推荐）
Hermes 的安全边界核心：七种后端 `local / docker / ssh / modal / daytona / vercel_sandbox / singularity`。
```yaml
terminal:
  backend: docker
  docker_image: ...
  docker_env: {...}          # env 白名单
  docker_volumes: [...]
  container_cpu: 1
  container_memory: 5120
```
**平台形态**：「执行环境」页 + 预设模板（local / docker 起步）。键数量适中、语义清晰，
而且是用户最该被引导配对的项（默认 local 直接跑终端命令，可视化能显著降低风险）。

### 2. `mcp_servers` — MCP 服务器（强烈推荐）
stdio（command/args/env）与 http（url/headers）两类，外加 sampling 配额
（model / max_rpm / allowed_models）。与供应商管理完全同构，表格化 CRUD 即可。
MCP 是 Hermes 生态的主要扩展面，值得独立导航项。

### 3. `SOUL.md` + `agent.personalities` — 人格与身份（推荐）
`SOUL.md` 是系统提示第一槽位，是用户改动频率最高的文件之一。
**平台形态**：带版本备份（复用 config_store 的备份机制）的 Markdown 编辑器 +
 personalities 的简单列表管理。工程量小、感知强。

### 4. `auxiliary.*` — 辅助模型（推荐）
vision / title_generation / compression / approval / triage_specifier 等小任务模型路由，
每项 `{provider, model, base_url, reasoning_effort}`。
**平台形态**：直接复用供应商选择器，做成「辅助模型」单页。
对成本优化价值大（vision 用便宜模型是常见诉求）。

### 5. `updates.*` — 更新策略（随更新功能一起）
`pre_update_backup: quick|full|off`、`backup_keep`、`non_interactive_local_changes: stash|discard`。
服务管理页已有「检查更新」按钮，把这三个键做成更新确认弹窗里的选项即可。

### 6. `cron/` 目录 — 定时任务（推荐，只读起步）
`~/.hermes/cron/` 下的任务文件 + `cron.catch_up_missed`。
**平台形态**：先做只读列表（时间/状态/最近执行），启停经 `cronjob` 工具链路；
直接写任务文件风险高，不建议首版就做编辑。

---

## 🟡 待评估（有价值但需权衡）

| 配置域 | 内容 | 权衡点 |
|---|---|---|
| `smart_model_routing` | 简单问题切廉价模型：`enabled / max_simple_chars / cheap_model.*` | 规则细节多；建议只暴露「开关 + 两个字数阈值 + 廉价模型选择」四个控件 |
| `memory` 可插拔后端的深度管理 | 各 provider 的高级键（Honcho 辩证推理参数、Mem0 OSS 模式、Supermemory 多容器等） | 一期已覆盖各方案的核心连接参数；高级键键位多且随上游演进，先引导 `hermes memory setup` 官方向导 |
| `compression.*` | 阈值/保护窗口/卫生检查等 20+ 键 | 键多而相互耦合；建议只暴露 4-5 个常用键做「性能调优」卡片，其余高级折叠 |
| `tool_loop_guardrails` + `agent.{max_turns, run_budget_seconds}` | 工具循环硬停、最大轮数、预算 | 键少价值高（成本护栏）；但语义偏危险，需配确认流 |
| `gateway.*` | signal_interrupt_grace_timeout、trust_env | 与外部服务管理器预算联动，改错影响停机行为，谨慎暴露 |
| `stt / tts / browser / image_gen` | 语音与多媒体供应商路由 | 多模态扩展面，按用户反馈排期 |
| `privacy.*` / `secrets.*` | PII 脱敏开关；secrets.command 启动期密钥注入 | 企业强需求；但 secrets.command 涉及凭证链，需安全评审后做 |
| 渠道高级规则 | feishu `group_rules`（per-chat policy/allowlist/blacklist）、discord `free_response_channels` | 已以 JSON 高级编辑兜底；可视化表单待真实使用反馈后定形态 |

---

## ⚪ 建议手改（不集成）

| 配置域 | 原因 |
|---|---|
| `database.*` / `runtime.*` | journal_mode、nofile 上限——改动影响数据安全与句柄上限，属于「装好就不动」的键 |
| `display.*` / `streaming.*` | 纯观感偏好（横幅、工具过程显示级别），价值低，做了反而增加维护面 |

---

## 附：渠道字段核对状态

平台按「诚实标注」原则区分了字段可信度：

- ✅ **完整核对**：`feishu`（30+ 个 FEISHU_* 键逐一对照官方文档）、`weixin`（platforms.weixin.extra + dm_policy/group_policy）、`discord/telegram/webhook/slack` 的 config.yaml 侧键；
- ⚠️ **待核对（UI 中标注「以文档为准」）**：`qqbot / wecom / dingtalk / telegram token / discord token / slack token / email` 的具体 .env 键名——这些渠道的接入流程是 `hermes gateway setup` 交互式向导，官方文档页存在但本平台尚未逐键比对。使用这些渠道时，请以控制台卡片上的「官方文档」外链为准。

修复方式很简单：在 `app/hermes/schema.py` 的 `PLATFORMS` 中补全 FieldDef 并把 `verified` 置 True，
表单、校验、保存路径全自动生效（描述符驱动的单点维护）。
