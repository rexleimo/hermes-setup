# Changelog

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
