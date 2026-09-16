# 架构说明

## 分层

```
┌──────────────────────────────────────────────────────────┐
│  web/  （FastAPI 路由 + Jinja2/HTMX 服务端渲染）             │
│    routers/*    页面与动作，薄控制器，只做参数收集与审计      │
│    deps.py      认证守卫 / CSRF / 客户端 IP                  │
├──────────────────────────────────────────────────────────┤
│  core/ （平台自身：与 Hermes 无关的基础设施）                 │
│    settings / db / security / sessions / csrf /            │
│    ratelimit / audit / appsettings / filelock /           │
│    backup / maintenance                                   │
├──────────────────────────────────────────────────────────┤
│  hermes/ （Hermes 适配层 —— 对底层操作的全部收敛点）          │
│    paths → schema → config_store → *_service               │
│    supervisor（进程）  installer（任务）  model_catalog（HTTP）│
├──────────────────────────────────────────────────────────┤
│  Hermes Agent 本体：~/.hermes/{config.yaml, .env, logs/}    │
│  以及 hermes gateway 进程                                    │
└──────────────────────────────────────────────────────────┘
```

依赖方向严格单向：`web → core/hermes`，`hermes → core`。
Web 层永远不直接触碰文件系统或进程；对 Hermes 的一切操作都经过 `app/hermes/` 包。
这个收敛点是未来替换底层实现（例如 Rust 守护进程，见 RUST_ASSESSMENT.md）的唯一接缝。

## 关键设计决策

### 1. 服务端渲染 + HTMX，而非 SPA
管理后台的交互密度（表格 CRUD、表单、轮询）用 HTMX 的局部交换完全够用，
换来的是：零构建链、零 node_modules、CSP 可以禁用内联脚本、部署产物就是几个静态文件。
Toast（`HX-Trigger` 事件）、弹窗（原生 `<dialog>`）、轮询刷新（`hx-trigger="every Ns"`）
均不依赖自定义框架代码（`static/js/app.js` 约 200 行原生 JS）。

### 2. 平台元数据与 Hermes 配置分离
- **平台 DB（SQLite）**：供应商显示名/备注、模型目录（display_name / context_length）、
  备选链草稿、用户 / 会话 / 审计。可以自由建索引、关联，不怕污染 Hermes。
- **Hermes 文件（config.yaml / .env）**：只写官方 schema 认可的键。
  渲染列表时以 `provider_meta` 为主数据源，同时读取 config.yaml 校验 `is_main` 等真实状态，
  保证「手改过配置文件」之后控制台不会说谎。

### 3. secrets 只进 .env
Hermes 官方支持 `key_env` / `${VAR}` 引用，平台借此把 API Key 与渠道令牌全部写入
`~/.hermes/.env`（chmod 600），`config.yaml` 只保存键名。UI 全程不回显密钥
（只显示「已设置」），编辑语义为「留空 = 不变、`__CLEAR__` = 删除」。

### 4. config.yaml 的无损编辑
ruamel.yaml round-trip 模式保留注释与格式；写入走
`fcntl 文件锁 → 备份（保留 10 份）→ 临时文件 fsync → os.replace 原子替换`。
Hermes 正在运行时读写同一文件也是安全的（原子替换保证读者要么看到旧版要么看到新版）。

### 5. 进程控制走官方 CLI
`hermes gateway start/stop/restart/status` 由 Hermes 自己完成守护化与 pid 管理，
平台不做 superset 的进程监管（避免与官方行为打架）。状态由三路信号合成：
CLI 文本、`gateway_state.json`、gateway.log 新鲜度；任一路径失败不影响其余。

### 6. 认证短路用异常而非返回值
FastAPI 新版不再对「依赖返回 Response」短路路由，因此 `require_login` / `require_admin`
抛出 `LoginRequired` / `Forbidden` 异常，由全局 exception handler 统一转成
302 跳转 / HX-Redirect / 403 页面。CSRF 是 router 级依赖（`csrf_guard`），
在依赖内 `await request.form()` 与 FastAPI 的表单解析共享同一份缓存，
避免了中间件消费请求体的经典坑。

## 数据流示例：把 OpenRouter 设为主模型

```
供应商详情页表单
  → POST /providers/openrouter/set-main   (csrf_guard → require_admin)
  → providers_service.set_main_model()
      → config_store.load_config()            # ruamel round-trip
      → model.{provider,default,context_length} 写入
      → config_store.save_config()            # 锁 → 备份 → 原子替换
      → audit.record("provider_set_main")     # 审计留痕
  → 303 + HX-Trigger toast
```

## 扩展点（二期候选，见 CONFIG_CATALOG.md）

- **执行环境（terminal.*）**：schema.py 的描述符模式可直接复制出「执行环境」表单；
- **SOUL.md 编辑器**：带版本备份的 Markdown 编辑，复用 config_store 的备份机制；
- **cron 任务**：读 `~/.hermes/cron/` 目录渲染 + 通过 cronjob 工具启停；
- **Rust 特权守护进程**：替换 `hermes/supervisor.py` 与 `config_store.py` 的实现，
  接口不变（见 RUST_ASSESSMENT.md）。

> v0.8.0 已落地的扩展点：**MCP 服务器**（`mcp_service`，与供应商同构的 CRUD +
> 官方 optional-mcps 目录）、**Skill 管理**（`skills_service`，skills/ 目录 +
> `skills.*` 配置域）、**插件与 Shell Hooks**（`plugins_service` / `hooks_service`，
> 白名单模型 + `hooks.*` CRUD，机制详见 PLUGINS_AND_HOOKS.md）。
