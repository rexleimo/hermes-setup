# 渠道接入助手（Channel Onboarding）设计

版本：M1（v0.4.0，weixin 先行）· 作者讨论于 2026-09 用户体验反馈

## 问题

渠道配置页此前是"照抄官方文档的表单"：要求用户

1. 自己读文档理解去哪拿 `account_id` / `App ID` / token；
2. 自己开终端跑 `hermes weixin`、`pip install aiohttp cryptography`——但用户面对的是可视化控制台，根本没有终端心智；
3. 二维码只出现在终端 ASCII 里，浏览器用户完全看不到。

结论：**文档怎么写不代表产品怎么做。凡是程序能代办的，不允许转嫁给用户。**

## 原则（所有渠道通用）

| 原则 | 含义 |
|---|---|
| P1 零终端 | 依赖检测/安装、登录、回填全部在网页内完成 |
| P2 二维码进浏览器 | 扫码类登录（weixin 等）直接把 QR 渲染在页面上，轮询状态 |
| P3 结果自动回填 | 登录拿到的 account_id/token 由程序写入 config.yaml，用户不抄不填 |
| P4 引导代替甩链接 | 表单类渠道内嵌分步指引（点哪个菜单、复制哪个字段），外链仅作补充 |
| P5 复用 hermes 本体 | 通过 hermes venv 调 `gateway.platforms.*`，不在 console 重实现协议 |

## M1 实现（本次落地）

- `app/hermes/onboarding.py`
  - `agent_python()/agent_repo()`：venv 定位（标准布局 + 从 bin 反推兜底）；
  - `deps_status()/install_deps()`：import 探测（60s 缓存）+ 后台 job `uv/pip install -e ".[messaging]"`，日志尾部进面板；
  - 驱动脚本：跑在 hermes venv 里，复用 `gateway.platforms.weixin` 的 `_api_get/save_weixin_account`，向 stdout 打 `EVENT {json}` 机器事件（qr / status / success / error），登录成功即写 `~/.hermes/weixin/accounts/`；
  - `qr_state()`：解析最近一次任务日志的事件流 → 面板状态机 idle/starting/qr/scaned/confirmed/error；二维码过期由驱动自动刷新（≤3 次），前端无感；
  - `apply_weixin_account()`：account_id 回填 `platforms.weixin.extra` 并启用渠道（幂等护栏在 router 层）。
- `app/web/routers/channels.py`：`GET /{name}/onboard`（HTMX 轮询片段，confirmed 时触发回填+审计）、`POST /{name}/qr-start`、`POST /{name}/deps-install`（两者 Admin only）。
- `channels/_onboard.html`：依赖行 + 二维码区（vendored `qrcode.min.js` 本地渲染，CSP `script-src 'self'` 不破）+ 状态文案 + 失败重试；仅在有活跃任务时轮询（2s），confirmed/error 自动停。
- `detail.html`：weixin 隐藏"填凭证三步"引导条，接入助手置顶；`account_id` 字段降级为高级手填（帮助文案改为"扫码后自动回填"）。
- 审计事件：`channel_qr_start` / `channel_deps_install` / `channel_onboard_backfill`。

## 验收（已在线验证部分）

- [x] /channels/weixin 顶部出现「接入助手」，本机显示"依赖已就绪"+"开始扫码连接"
- [x] 非 weixin 渠道 onboard → 404；未登录 → 302；operator POST → 403
- [x] EVENT 流解析、confirmed→自动回填 config.yaml 单测覆盖（tests/test_onboarding.py）
- [ ] 真机微信扫码全链路（需用户手机配合，逻辑与 hermes 官方向导同源）

## M1.1 热修（v0.7.0，用户反馈：任务结束但二维码没出来）

**根因**：`installer.submit` 把子进程环境整体替换成硬编码 POSIX PATH（不含
System32）。Windows 下 venv python 因此无法初始化 Winsock
（`OSError: [WinError 10106]`），扫码驱动在 `import asyncio` 即崩溃，日志里
没有任何 `EVENT` 行，面板只剩兑底文案「任务已结束但未产生二维码」。

- `installer.submit`：改为继承控制台进程完整环境（`dict(os.environ)` + HOME 兑底），
  同时修复 install/update/deps/qr 全部任务类型在 Windows 下的同类问题；
- `qr_state()`：error 阶段附带 `log_tail`（剔除 EVENT 机器行的日志尾部），
  `_onboard.html` 直接展示真实报错 + 一键反馈链接——小白不需要会翻日志；
- 真机验证：驱动在 hermes venv 内稳定输出 `EVENT {"type":"qr",...}`。

## M2 进展

1. **白名单免查 ID**：未实现。
2. ✅ **表单渠道内嵌分步指引**（v0.7.0）：`PlatformDef.guide_steps: tuple[GuideStep]`
   （text + 可选 url/link），detail 页渲染「接入引导」步骤卡；所有需要外部平台
   操作的渠道（飞书/Telegram/Discord/Slack/QQ/企业微信/钉钉/Email/WhatsApp/
   Signal/Matrix/Webhook/API Server）均带可点击直达链接（如 t.me/BotFather、
   open.feishu.cn/app），官方文档降为右上角「补充阅读」；单测断言除 weixin 外
   所有渠道必须有带链接的引导。
3. **onboarding 能力声明化**：部分完成（guide 已声明化；qr 仍按 weixin 硬编码）。
4. 其他扫码类（whatsapp 等）套用同一驱动模式：未实现。
