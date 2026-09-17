# 安全说明

Hermes Console 是**权限极高的运维入口**（它能改模型路由、写渠道令牌、启停 Gateway），
所以它按"公网可达的管理后台"标准来做防护，而不是按内网小工具。

## 平台已内置的防护

| 控制点 | 一句话说明 |
|---|---|
| 密码存储 | Argon2id 哈希，平台不保存明文；密码策略 ≥10 位且含字母数字 |
| 两步验证 | 每用户可绑定 TOTP（RFC 6238）；管理员可重置成员 2FA |
| 会话 | 服务端存储、可随时吊销；HttpOnly + SameSite=Strict；绝对过期与空闲过期双闸 |
| CSRF | 全部变更请求做令牌校验（含 HTMX 局部请求） |
| 登录限流 | 单 IP 频次限制 + 单账号连续失败锁定，尝试全部入审计 |
| 权限分级 | `admin` / `operator` 两档；「最后一名启用中的管理员」不可停用或降级 |
| 审计 | 仅追加日志：登录、全部配置变更（含被拒绝的）、后台任务 |
| 密钥处理 | API Key 只写 `~/.hermes/.env`（自动 600 权限），界面只显示"已设置"、永不回显 |
| 配置写入 | 校验 → 备份（保留 10 份）→ 原子替换 → 文件锁防并发，注释完整保留 |
| 访问控制 | 支持 `HERMES_CONSOLE_ALLOWED_IPS` 做来源 IP 白名单（fail-closed） |

实现细节、资产清单与内部工程文档见 [docs/dev/THREAT_MODEL.md](dev/THREAT_MODEL.md)
（工程文档，不在产品界面与首页中链接）。

## 首次部署加固清单（用户侧要做的事）

平台能做的到这里为止，剩下的由部署方式决定：

1. **设置强随机的 `HERMES_CONSOLE_SECRET`**，否则每次重启全体会话失效：
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. **不要把控制台端口裸暴露公网**。默认监听 `0.0.0.0:8420`（含本机回环，开箱即用）；
   只在本机使用可改回 `HERMES_CONSOLE_HOST=127.0.0.1`；需要远程管理时，
   走 VPN / 内网，或用 Nginx·Caddy 终止 TLS 后配 `HERMES_CONSOLE_ALLOWED_IPS` 限来源；
3. 前置 HTTPS 时开启 `HERMES_CONSOLE_SECURE_COOKIES=1`；
4. 初始化完成后**立即给管理员绑定 TOTP**；
5. 日常操作使用 `operator` 账号，`admin` 只用于成员与系统设置；
6. 定期看 `/audit`，重点关注被拒绝（denied）与失败（failed）的条目。

## 发现漏洞怎么办

请**不要**开公开 Issue。任选一条：

- GitHub → Security → **Report a vulnerability**（私有漏洞报告，走 `rexleimo` 通知）；
- 或提交 Issue 时**只写标题不写细节**，由维护者转私有通道跟进。

修复随版本发布并在 [CHANGELOG.md](../CHANGELOG.md) 标注；确认者可选择被署名为贡献者。
