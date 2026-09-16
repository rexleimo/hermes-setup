# Changelog

## 0.7.0 — 2026-09-17（扫码修复 + 全渠道接入引导）

### Added
- **扫码回填小白默认两件**：① 自动设 `platforms.weixin.home_channel` 为号主私聊，
  消除反复出现的「📬 No home channel is set… Type /sethome」催促；② 自动把
  `display.busy_input_mode/busy_text_mode` 设为 queue，忙时发新消息不再打断当前
  任务（消除莫名其妙的「↪ Redirected current run」）。
- **Windows 配置保存重试**：`os.replace` 目标被 Gateway 监控句柄/杀软短暂占用时
  抛 WinError 5，三处原子写入统一退避重试，偶发锁不再变成「保存失败」。
- **扫码回填后自动重启 Gateway**：每次扫码都会作废上一个 iLink 会话，运行中的
  Gateway 若持旧 token 会 `Session expired` 静默丢消息；现在面板检测到新账号回填
  且 Gateway 在运行时自动重启，并在面板提示重启结果。
- **任务台账（操作记录）**：「安装与更新」页新增「最近任务」卡片——后台任务（安装/更新/扫码接入等）
  不再跑完就消失；历史列表带中文标签、状态、起止时间，点「查看输出」展开任意任务的完整日志。

### Fixed
- **网关无法启动 / 状态永远「未知」**（用户反馈，实机三连环）：
  1. `supervisor._child_env()` 用 POSIX keep 白名单过滤环境变量，Windows 子进程缺
     SYSTEMROOT/USERPROFILE → hermes CLI 直接 `RuntimeError: Could not determine home
     directory`。现改为继承完整环境、仅剔除 `HERMES_CONSOLE_*` 敏感键；
  2. `_path_with_common_bins()` 用 `:` 拼 PATH（Windows 是 `;`）→ 子进程找不到 node；
     另补 Windows 常见 npm 全局目录；
  3. CLI 输出按本地 GBK 解码崩溃（中文 Windows）→ 固定 `encoding=utf-8`；
     `signal.kill` 在 Windows 不存在 → `_pid_alive` 改 OpenProcess 查询（绝不可用
     os.kill(pid,0)，Windows 上会直接杀掉目标进程）；
  4. 真正的启动拦路虎：扫码回填后 weixin `dm_policy: open` 且无白名单，hermes 安全
     护栏拒绝启动（Refusing to start）——**现在扫码成功即自动把号主写入
     WEIXIN_ALLOWED_USERS 并把 dm_policy 收敛为 allowlist**（幂等、不覆盖已有名单）；
     护栏拦截时页面给出「【控制台解读】」人话提示，不再静默显示未知；
  5. 状态关键词补充 `no gateway process detected` 等实机输出，不再误判未知。
- **有任务运行时 `/service` 页 500 无法使用**（用户反馈）：整页上下文只传 `active_job`，
  而内嵌的 `_job_panel.html` 读的是 `job`/`job_lines`/`done`——扫码任务真能跑满几分钟
  之后这个潜伏 bug 必现（此前任务秒挂所以从未触发）。整页与 HTMX 片段改用同一个
  上下文构造函数；无任务时面板返空壳不再抛 UndefinedError。
- **孤儿任务永久卡 running**：服务被强杀时正在跑的 job 行停在 running，既堵死后续
  `submit`（「已有任务在执行中」）又让 /service 反复轮询旧面板。现在启动时
  `reap_orphan_jobs()` 自动回收（标 failed/-9，日志追加中断说明）。
- **扫码任务在 Windows 上必挂、二维码永远不显示**（用户反馈：「任务已结束但未产生二维码」）：
  `installer.submit` 把子进程环境整体替换为硬编码 POSIX PATH，导致 hermes venv 的 python
  无法初始化 Winsock（WinError 10106），扫码驱动 `import asyncio` 即崩溃。现改为继承完整环境，
  同时修复安装/更新/依赖类任务在 Windows 下的同类问题；真机验证驱动已能稳定输出二维码。
- **接入助手错误不再吞日志**：失败时面板直接展示驱动真实报错（log_tail，EVENT 机器行过滤）
  + 一键反馈链接，小白无需翻 `data/jobs/*.log`。

### Added
- **小白一键启动**：`start.bat`（Windows 双击即用）/ `start.sh`（macOS · Linux）——
  自动检测并安装 uv、自动 `uv sync`、启动后 3 秒自动打开浏览器，关窗即停；
  README 快速开始改为「双击优先」，原命令行步骤折叠为开发者选项。
- **全渠道「接入引导」步骤卡**（小程序化理解成本目标）：`PlatformDef.guide_steps` 声明化，
  飞书/Telegram/Discord/Slack/QQ/企业微信/钉钉/Email/WhatsApp/Signal/Matrix/Webhook/API Server
  逐渠道内嵌分步指引，每步能直达的就给可点击链接（如 @BotFather、飞书开放平台、
  Discord Developer Portal），官方文档降为「补充阅读」；单测断言除 weixin（走接入助手）外
  所有渠道必须有带链接的引导。

## 0.6.0 — 2026-09-16（更名 + 图标体系）

### Changed
- **文件工作台 → 文件管理器**（导航、页面标题、顶栏同步更名；路由 /files 不变）。
- **弃用 emoji 图标**（跨系统渲染不一致）：新增内联 SVG 彩色文件类型图标宏 `wbicon`
  （folder/image/video/audio/document/code/archive/file/home/clock/drive/flask/archivebox，
  造型参考 Win11 Fluent，零依赖零构建）；侧栏、网格、列表、详细信息面板、
  导航箭头（chevron/arrow/rotate/sort/grid 线框图标）全部 SVG 化。

## 0.5.0 — 2026-09-16（文件工作台 OS 风格重构）

### Changed
- **文件工作台按 Finder/Explorer 交互重做**（用户反馈：不接受表格型页面）：
  - 侧栏 = 位置（工作区根 + 7 个标准目录含文件数）+ 智能集合
    （最近使用 / 图片 / 视频 / 音频 / 文档 / 代码 / 压缩包，全工作区按扩展名聚合，虚拟视图不复制文件）；
  - 主区 = 图标网格（图片真缩略图、视频首帧 `<video preload=metadata>`、类型 emoji 字形 + 扩展名徽标、
    目录子项计数）⇄ 详细列表一键切换；工具条含面包屑、筛选框（前端即时过滤）、排序（名称/时间/大小）；
  - 交互 = 单击选中（选中操作条：打开/下载/打包/归档）、双击打开（目录进入/文件右侧 Quick Look 预览）、
    右键上下文菜单、Esc 取消、Enter 打开、Backspace 上一级、选完文件即上传；
  - 导航改 hx-boost 整页（URL 即状态，浏览器前进/后退原生可用）。
- 工作区目录体系扩展：`documents/ pictures/ videos/` 成为标准目录（init 幂等补齐，
  散乱文件巡检白名单同步）；上传仍固定落 downloads/。
- 预览面板支持视频播放与音频播放（原生 `<video>/<audio>`，零依赖）。

### Added
- workspace_service：CATEGORIES 类型体系、list_collection / list_recent /
  category_counts / location_counts、Entry.count（目录子项数）、排序参数。

### Removed
- 旧三栏表格 `files/_list.html`。

### Tests
- 新增智能集合/布局/排序/目录计数 2 项；106 passed, 3 skipped。

## 0.4.0 — 2026-09-16（渠道接入助手 · weixin 浏览器内扫码）

### Added
- **接入助手（onboarding）**：微信渠道配置不再要求用户读文档、开终端。
  - 依赖检测 + 一键安装（`uv/pip install -e ".[messaging]"` 后台任务，日志实时进面板）；
  - 二维码直接渲染在网页里（vendored qrcode.min.js，CSP 不破），扫码状态 2s 轮询，
    过期自动刷新（≤3 次），全程零终端；
  - 登录成功自动回填 `platforms.weixin.extra.account_id` 并启用渠道，
    凭据由 hermes 自身写入 `~/.hermes/weixin/accounts/`（复用 `gateway.platforms.weixin`）。
- 新端点：`GET /channels/{name}/onboard`（HTMX 片段）、`POST qr-start`、`POST deps-install`
 （后两者 admin only）；审计事件 channel_qr_start / channel_deps_install / channel_onboard_backfill。
- 设计文档 `docs/CHANNEL_ONBOARDING_SPEC.md`：五原则（零终端/QR进浏览器/自动回填/
  引导代替甩链接/复用hermes本体）+ M2 计划（白名单免查ID、表单渠道分步指引）。

### Changed
- weixin 详情页：接入助手置顶，隐藏"填凭证三步"引导条；`account_id` 不再必填，
  帮助文案改为"扫码后自动回填"。

### Tests
- 新增 tests/test_onboarding.py（7 项）：EVENT 解析、confirmed 自动回填、
  非 weixin 404、登录门槛、operator 403。共 104 passed, 3 skipped。

## 0.3.1 — 2026-09-16（按钮样式修复）

### Fixed
- **链接型主按钮白底白字（隐形）**：基态选择器 `a.btn`（特异性 0,1,1）压过
  `.btn-primary` 等变体（0,1,0），叠加 `:visited` 后文字变白 —— 「前往供应商」
  「添加第一个供应商」等按钮渲染成空白块，hover 才短暂正常。基态改回 `.btn`。
- **空状态按钮图标被撑成 40px**：`.empty-state .icon` 后代选择器泄漏到按钮内，
  改为 `.empty-state > .icon` 只作用于容器直属装饰图标。
- 新增回归测试 `test_css_button_specificity_guard` 防止两条规则回退。

## 0.3.0 — 2026-09-17（W3：文件工作台体验版）

### Added
- **文件工作台**（`/files`，对应 FILE_WORKBENCH_SPEC M1+M3 骨架）：三栏式（目录树 / 列表 / 预览），
  浏览、内联预览（md/代码/图片/pdf）、单文件下载、目录打包 zip、上传（固定落 downloads/，
  同名自动加后缀永不覆盖）、一键归档（mv → archive/）。
- `app/hermes/workspace_service.py`：jail 化文件域服务 —— 根目录永远读 `EngSettings.workspace`
  （与工程注入层同源，无第二配置）；所有路径 resolve 后校验在根内，symlink 逃逸同样拦截。
- S2 防劫持：`validate_root` 拒绝过浅路径与 home 本身；`save_settings`/`init_workspace` 保存前强制校验。
- 归位巡检摘要（页头徽章）：根部散乱文件 / scratch 超期 / 项目缺 requirements.md（只读提示，执法 M2 再补）。
- 安全（S4/S5）：读=登录、写=管理员+CSRF；越狱尝试落 `files_denied` 审计；浏览/预览/下载/打包/上传/归档全量审计。
- 设置项 `workbench_upload_max_mb`（默认 50）。
- 新测试 9 项（jail、登录门槛、角色拦截、上传不覆盖、归档、zip、审计）；全套 99 项绿。

## 0.2.0 — 2026-09-16（W1 + W2）

### Fixed
- **Windows 可运行**：`config_store` 的 fcntl 硬依赖改为跨平台文件锁
  （`app/core/filelock.py`：POSIX flock / Windows msvcrt / 降级原子替换）；CI 增加 windows-latest。
- 时间基准统一 UTC（DB schema 默认值、sessions、ratelimit、job_runs、provider_meta），
  审计页列头标注 UTC；消除时区/夏令时导致的会话过期边界错误。
- 登录会话固定（session fixation）：登录成功后吊销旧 session id，另发新 id。

### Security
- TOTP 种子静态加密落库（`enc:v1:`，HMAC-CTR + encrypt-then-MAC，密钥派生自
  `HERMES_CONSOLE_SECRET`）；未设置固定密钥时降级明文并在启动日志/设置页显著警告。
- 用户读取统一走 `appsettings`（Row→dict + 透明解密），`deps.current_user` 同步收敛。

### Added
- `/healthz` 免认证探针；`/static`、`/healthz` 不再创建匿名会话行（防 sessions 注水）。
- DB 每日快照：`app/core/backup.py`（stdlib sqlite3 backup API，保留 5 份）+ 设置页列表与手动备份。
- 周期维护循环：`app/core/maintenance.py`（15 分钟：过期会话/limiter 修剪/到期快照）。
- 服务器端紧急工具 `scripts/console_admin.py`（重置密码/解 2FA/列会话/吊销/首建管理员）。

### Tests
- 新增 6 项（静态加密、UTC 基准、healthz 豁免、快照轮转、会话轮换）；
  POSIX-only 桩测加 skipif。Windows 本机 88 passed / 3 skipped。

## 0.1.0 — initial release
