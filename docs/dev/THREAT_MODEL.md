# 威胁模型与安全控制（内部工程文档）

> 归属：工程内部文档，**不面向产品使用者**，也不在对外首页/控制台 UI 中链接。
> 对外可见的安全说明只有 [docs/SECURITY.md](../SECURITY.md)（部署加固清单 + 漏洞报告渠道）。
> 改动本文件时，先确认新增内容是否会被误当成"给攻击者的地图"——只写已实现的防护，
> 未修补的缺口走 GitHub Private Vulnerability Reporting，不进仓库。

## 资产与威胁

Hermes Agent 本身权限极高（可执行终端命令、读写文件），配置中心一旦被攻破等于交出整台服务器。
因此本平台的安全设计目标是：**把控制台本身变成高成本目标，并把每一步操作留痕**。

| 资产 | 说明 |
|---|---|
| `~/.hermes/.env` | 全部 API Key 与渠道令牌 |
| `~/.hermes/config.yaml` | 模型路由、渠道、执行后端配置 |
| Gateway 进程控制 | 启停 = 对外服务可用性 |
| 平台账户 | 越权登录即获得以上全部 |

主要威胁：公网暴露的弱口令爆破、撞库、CSRF、会话劫持（含 XSS 窃取）、
密钥经 UI/日志泄露、配置文件被写坏导致 Hermes 异常。

## 控制矩阵

| 控制点 | 实现 |
|---|---|
| 密码存储 | Argon2id（argon2-cffi），平台不保存明文；密码策略 ≥10 位且含字母数字 |
| 会话 | 服务端存储（DB 可吊销）；HttpOnly + SameSite=Strict + Secure（HTTPS 下）；绝对过期（默认 12h）与空闲过期（默认 60min）双闸；改密/停用即吊销全部会话 |
| CSRF | 路由级依赖校验，令牌 = HMAC(session_id) 双提交；HTMX 请求经 body 级 `hx-headers` 注入 `X-CSRF-Token` |
| 登录限流 | 滑动窗口：单 IP 15 分钟 20 次；单账号连续失败 5 次锁定 30 分钟（防撞库，跨 IP 生效）；全部尝试入审计 |
| 两步验证 | 每用户 TOTP（RFC 6238），绑定走二维码/密钥 + 验证码确认；绑定后重置该用户其他会话的 2FA 状态；管理员可重置成员的 2FA |
| RBAC | `admin`（全部）/ `operator`（供应商/渠道/服务/链路，禁入用户管理与系统设置）；「最后一名启用中的管理员」不可停用/降级 |
| 审计 | 仅追加表：登录成败/锁定、全部变更（含被 CSRF/RBAC 拒绝的）、任务执行；支持筛选与分页 |
| IP 白名单 | `HERMES_CONSOLE_ALLOWED_IPS`（CIDR 列表），中间件层 fail-closed |
| 安全响应头 | CSP（`default-src 'self'; script-src 'self'`，无内联脚本）、X-Frame-Options: DENY、nosniff、Referrer-Policy |
| 密钥不回显 | UI 只显示「已设置」状态；日志与审计 detail 不含密钥；错误页不回显堆栈 |
| 子进程环境 | 调用 hermes 时继承完整环境但剔除 `HERMES_CONSOLE_*` 敏感键（Windows 下必须继承 SYSTEMROOT/USERPROFILE，见 CHANGELOG 0.7.0） |
| 配置写入完整性 | 校验（schema 层）→ 备份（10 份）→ 临时文件 fsync → `os.replace` 原子替换（Windows 短暂占用时退避重试）→ 跨平台文件锁防并发 |

## 部署边界（架构事实，不是缺口清单）

- 平台与 Hermes **同机同用户**运行 —— 控制台被攻破等同 Hermes 被攻破。所以控制台的定位是
  「高成本目标 + 全量留痕」，而不是「可公网随便摆的普通后台」；跨机的「特权代理」形态
  见 [RUST_ASSESSMENT.md](../RUST_ASSESSMENT.md)。
- 登录限流与锁定状态存在**本机 SQLite** —— 多实例部署前需先解决共享存储，否则锁定不跨节点生效。
- 未内置 fail2ban / PAM / WebAuthn 一类叠加能力；需要时在反向代理层自行叠加，
  平台不假设自己是最外层防线。

> 至于「哪些配置域还没做可视化护栏」（`terminal.*`、`mcp_servers`、`secrets.*` 等），
> 那是**排期信息**，统一记在 [CONFIG_CATALOG.md](../CONFIG_CATALOG.md)，
> 不在本文重复 —— 安全文档里堆「我们还没做 X」只会变成别人的购物清单。

## 对外表述纪律

| 场景 | 可以写 | 不要写 |
|---|---|---|
| README / 首页 | 用了哪些防护（Argon2id、TOTP、CSRF、IP 白名单、审计、密钥不回显） | 未防护项清单、绕过思路、内网端点与本机绝对路径 |
| 控制台 UI | 管理员自己机器上的真实路径与键位 | 内部规格文档、排期表、威胁矩阵 |
| 仓库 | 已实现的行为与契约 | 未修补漏洞的细节；「我们还没做 X」的缺口清单（排期归 CONFIG_CATALOG，漏洞走私有报告） |
