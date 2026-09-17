# Changelog

## 0.8.3 — 2026-09-17（root 安装的 bin 包裹脚本解析 + 仓库目录显示修正）

### Fixed
- **官方 root 安装的 bin 是 bash 包裹脚本而非软链**：`resolve()` 解不出仓库，
  反推仍落空（Ubuntu 干净容器 root 安装实测复现）。现增加两层兜底：解析包裹脚本
  `exec` 行（带仓库标记校验，防误判），以及官方固定落点 `/usr/local/lib/hermes-agent`、
  `/opt/hermes-agent`。
- **venv 解释器同样吃反推仓库**：FHS 下直连 venv 不存在，`agent_python` 会报
  "未找到 hermes venv"；现与仓库反推共用候选，扫码/依赖链路在 root 机上可达。
- **服务页"安装目录"显示修正**：改用反推结果，FHS 机器不再显示错误的家目录拼接。

### Tests
- tests/test_agent_repo.py 补到 10 项（软链/FHS 用例部分仅 POSIX）。

## 0.8.2 — 2026-09-17（Linux root 安装布局兼容 + start.sh 可执行位）

### Fixed
- **start.sh 没有可执行位**：git 记录为 100644，全新 clone 后直接运行报
  Permission denied（Ubuntu 干净容器实测复现）。已置 100755，与 scripts/*.sh 一致。
- **root/FHS 安装下源码目录找不到**：root 安装把 hermes-agent 放到
  `/usr/local/lib`（而非家目录），且 `/usr/local/bin/hermes` 多为软链；
  直连 `paths.agent_repo` 落空，连带 MCP/技能/插件目录、venv 探测、依赖安装一起失效。
  现收敛到 `paths.resolve_agent_repo()`（直连优先，否则解软链逐级上找），
  MCP/技能/插件目录与扫码/依赖链路共用；并删掉 `install_deps` 里用直连路径
  覆盖反推结果的一行。

### Tests
- 新增 tests/test_agent_repo.py（10 项；软链/FHS 用例部分仅 POSIX）。
- Ubuntu 24.04 干净容器端到端验证：一键安装 exit 0（约 3.5 分钟），检测链
  `installed=True`，`hermes update --check` 只读验证通过。

## 0.8.1 — 2026-09-17（Windows 一键安装走原生通道 + 全新机默认家目录修正）

### Fixed
- **Windows 一键安装曾跑 Linux 安装脚本**：任何平台都执行 `curl | bash` 装 `install.sh`
  （官方头注仅支持 Linux/macOS/Termux）；Windows 上 `bash` 解析到 WSL 存根，
  装不出原生版，且任务台账从无成功记录。现按平台选择安装器：Windows → 官方
  `install.ps1`（带 `-SkipSetup -NonInteractive`，后台无 TTY 不会挂死），其余平台不变；
  安装前网络预检、页面复制框、体检探测同步使用同源 URL。
- **全新 Windows 机默认家目录错位**：默认 `~/.hermes`，而官方安装器落到
  `%LOCALAPPDATA%\hermes`，配置读写会进错目录。现默认与官方落点一致
  （DB/环境变量显式配置仍优先）；检测新增 `%LOCALAPPDATA%\hermes\bin\hermes.exe`
  绝对路径兜底——安装后用户 PATH 未刷新也不用重启控制台。
- **安装失败无提示**：`/service` 模板从未渲染 `error` 变量，预检拒绝白白返回 400。
  现页面顶部显示错误横幅；安装方式与安装目录改为平台相关变量，不再写死。
- **运行体检**：新增 Git 检查（一键安装需 Git 下拉仓库）；Windows 探测清单改为
  PS 安装器域名 + GitHub + astral + PyPI；成功提示不再写死条数。
- **安装/更新任务改用 jobs_dir 为工作目录**：此前继承控制台进程目录（即仓库根），
  实测出现 PowerShell 模块缓存（`Microsoft/`）落到仓库里。官方安装器全用绝对路径，
  换目录不影响安装结果。

### Notes
- `hermes update`（官方 CLI 子命令，`--check/--plan` 可只读试运行）与扫码/依赖链路未动。
- 已知后续：个别记忆方案的后台安装命令仍是 POSIX 写法（本机曾失败 exit 2），排期下一批。

### Tests
- 新增 tests/test_install_platform.py（7 项）；全量回归除 `test_backup_keeps_last_five`
  （Windows 文件锁旧疾，主分支同样失败，与本期无关）外全绿。
- 另在隔离沙盒（独立控制台数据目录 + 独立家目录）触发真实一键安装：exit 0，
  安装目录/venv/CLI 俱全；冷缓存 `uv sync` 与 `check_live` 全页自检同样通过。

## 0.8.0 — 2026-09-17（扩展生态：Skill 管理 + MCP 服务管理 + 插件与 Hook）

### Added
- **MCP 服务管理（`/mcp`）**：`mcp_servers.*` 的 CRUD 与启停——stdio（command/args/env）
  与远程 HTTP/SSE（url/headers/OAuth 2.1）两类，信任分级（untrusted = 写操作走审批）、
  超时可调；**密钥只写 `.env`（600 权限），配置中以 `${VAR}` 占位符引用**（官方
  secret-scope 语义，连接期解析）；编辑时自动保留存量密钥引用；删除时联动清理该服务
  写入 `.env` 的密钥变量；`agentmemory` 打「记忆系统托管」标签并拒绝在本页删除。
  **官方热门目录一键添加**：解析本机 `agent_repo/optional-mcps/*/manifest.yaml`
  （64 个 Nous 审核条目），热门条目徽标置顶，安装结果与官方 `hermes mcp install` 等效
  （不落多余键，安装提示透传）。
- **Skill 管理（`/skills`）**：`~/.hermes/skills/` 目录可视化——解析 SKILL.md
  frontmatter（name/description/version/tags），来源标签（工程规范托管 / 官方目录 /
  自定义）、缺 SKILL.md 的无效目录标记；启停写入 `skills.disabled`
  （`hermes-agent` 为官方 ESSENTIAL_SKILLS 拒绝禁用）；删除需输确认词且保护工程托管技能；
  **官方热门技能库**：解析 `agent_repo/optional-skills/`（24 类）按类目折叠展示，
  一键安装 = copytree（与 `hermes skills install` 等效，防路径穿越与重名）；
  **skills 配置域表单**：external_dirs（兼容 list 与 JSON 字符串两种存量写法）、
  project_discovery / template_vars / inline_shell(+timeout) / guard_agent_created
  （默认值不落键）、trusted_project_dirs 展示与取消信任（等效 `hermes skills untrust`）。
- **插件与 Hook 管理（`/plugins`）**：
  - *插件*：扫 `~/.hermes/plugins/*/plugin.yaml`，启停即增删 `plugins.enabled`
    白名单（官方信任模型：默认禁用）；白名单残留（目录已删）识别与一键清理；
    `plugins.hook_callback_timeout` 调优；官方策展目录 `plugin-catalog/*.yaml`
    展示（tier/capabilities/需注入的 env）；安装走官方 CLI
    （`hermes plugins install`，sha pin 与黑名单校验全部交给官方实现），
    复用后台任务台账（新 kind：安装插件）。
  - *Shell hooks*：`hooks.<event>[]` 的 CRUD，校验对齐官方——事件须在 VALID_HOOKS
    （37 个全集随源码内置）、matcher 仅 pre/post_tool_call 且须为合法正则、
    fail_closed 仅 pre_tool_call、timeout 1-300；同 (event, command) 覆盖即编辑；
    `hooks_auto_accept` 开关；信任白名单（shell-hooks-allowlist.json）展示与
    单条撤销（等效 `hermes hooks revoke`）。
  - *只读盘点*：gateway hooks 目录（HOOK.yaml 的 name/events）、outbound webhooks。
- **调研沉淀**：[docs/PLUGINS_AND_HOOKS.md](docs/PLUGINS_AND_HOOKS.md)——Hermes 四套
  Hook 体系（shell hooks / plugin hooks / gateway hooks / outbound webhooks）与插件
  信任模型的完整结论，即本次「插件机制调研」的交付物。

### Changed
- 侧栏新增「扩展生态」分组（技能管理 / MCP 服务 / 插件与 Hook），新增三个内联 SVG 图标；
- `docs/CONFIG_CATALOG.md` 与「配置项全景」页：`mcp_servers`、`skills` 移入「已集成」，
  新增 `plugins.enabled`、`hooks` 两域条目（已集成 6 → 11）；`docs/ARCHITECTURE.md`
  扩展点同步勾掉 MCP；README 功能总览、目录结构、写入契约与测试计数更新；
- `check_live.py` 增加三个新页面的探针。

### Notes
- 写入纪律不变：全部走 `config_store`（文件锁 → 备份 → 原子替换，注释保留），
  默认值不落键，真实状态一律以 config.yaml / 文件系统为准（控制台不说谎）；
- 本期不做：技能/插件远端 marketplace 搜索、per-platform 技能禁用
  （`skills.platform_disabled`）、outbound webhook 编辑、gateway hooks 的
  Python handler 编辑。

## 0.7.1 — 2026-09-17（对外面收敛：一份首页 + 真实截图）

### Added
- **README 配图**：`scripts/make_screenshots.py` 拉起一个**隔离演示实例**（独立
   `HERMES_CONSOLE_DATA` / `HERMES_HOME`，`HERMES_BIN` 指向不可执行的空桩）后用 Playwright 截图，
   配方可重现（`docs/SCREENSHOTS.md`）；入库的是 `docs/assets/*.webp`（单张 <200 KB），
   原图落 `shots/`（已 gitignore）。截图不读也不写机器上真实的 `~/.hermes`，也不会调用真实 hermes CLI。
- **文档分层**：新增 `docs/dev/`（工程内部文档）与 `docs/SCREENSHOTS.md`；
   `docs/` 目录清单补齐了两份之前没列进 README 的规格（渠道接入助手、文件管理器）。

### Changed
- **对外只留一份首页 = README**。文档正文在 GitHub 直接渲染阅读，不再自建文档站。
- **`docs/SECURITY.md` 重写为“给部署者的安全说明”**：保留已内置防护清单 + 首次部署加固清单，
   新增「发现漏洞怎么办」（走 GitHub 私有漏洞报告，不开公开 Issue）；
   原来的「资产与威胁 / 控制矩阵 / 已知边界」迁入 `docs/dev/THREAT_MODEL.md`，
   并把「已知边界」改写成中性的「部署边界」（剔除 CSP 指令细节与缺口清单式表述，
   排期信息归 `CONFIG_CATALOG.md`），另补一节「对外表述纪律」（首页/UI/仓库分别能写什么）。

### Removed
- **`site/` 单页与 `.github/workflows/deploy-site.yml` 已删除**（GitHub Pages 未启用，
   且 `rexai.top` 已不再指向本项目 —— 该域名与相关 DNS/部署步骤全部从文档中移除，不要再引用）。
   原首页把 `docs/SECURITY.md` 的威胁矩阵与 `docs/CONFIG_CATALOG.md` 的**未实现功能排期**
   直链给任意访客，相当于递出攻击面清单；这是本次收敛的直接动因。

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
