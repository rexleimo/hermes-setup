# 安全模型

Hermes Agent 本身权限极高（可执行终端命令、读写文件），配置中心一旦被攻破等于交出整台服务器。
因此本平台的安全设计目标是：**把控制台本身变成高成本目标，并把每一步操作留痕**。

## 资产与威胁

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
| 子进程环境 | 调用 hermes 时仅传白名单环境变量（PATH/HOME/LANG…），不透传平台自身的敏感变量 |
| 配置写入完整性 | 校验（Pydantic/schema 层）→ 备份（10 份）→ 临时文件 fsync → `os.replace` 原子替换 → `fcntl` 文件锁防并发 |

## 首次部署加固清单

1. 设置强随机的 `HERMES_CONSOLE_SECRET`（否则每次重启全体会话失效，且签名密钥不可预测性依赖进程熵）；
2. 反向代理终止 TLS，并开启 `HERMES_CONSOLE_SECURE_COOKIES=1`；
3. 配置 `HERMES_CONSOLE_ALLOWED_IPS`（办公网/VPN 网段），不要把 8420 裸暴露公网；
4. 初始化后立即给管理员绑定 TOTP；
5. 为日常操作创建 `operator` 账号，`admin` 仅用于成员与系统设置管理；
6. `~/.hermes/.env` 与平台 SQLite 的所在目录权限收紧至运行用户（平台已自动对 .env 执行 600）；
7. 定期复核 `/audit`（关注 `outcome=denied/failed` 的条目）。

## 已知边界（诚实声明）

- 平台与 Hermes 同机部署、同用户运行 —— 平台被攻破即等同 Hermes 被攻破。
  跨机的「特权代理」形态见 [RUST_ASSESSMENT.md](RUST_ASSESSMENT.md) 的二期方案；
- CSP 中 `style-src 'unsafe-inline'` 保留（少量内联样式），脚本域严格 `'self'`；
- 登录锁定状态存于本机 SQLite，多实例部署时需共享存储后重估；
- 未内置 fail2ban/PAM/MFA 硬件密钥（WebAuthn），如需可叠加在反向代理层。
