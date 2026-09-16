"""平台自身配置：环境变量优先，数据库可覆盖部分运行时项。

设计原则：
- 安全敏感项（密钥、Cookie 策略、IP 白名单）只从环境读取，不允许通过 Web 修改；
- 部署相关运行时项（Hermes 路径、会话时长）存 DB，可在「系统设置」页调整。
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv() -> None:
    """最小 .env 加载：KEY=VALUE，已存在的环境变量优先。"""
    path = Path(__file__).resolve().parents[2] / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    """不可变的进程级配置（env）。"""

    host: str = field(default_factory=lambda: _env("HERMES_CONSOLE_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("HERMES_CONSOLE_PORT", "8420")))
    secret_key: str = field(
        default_factory=lambda: _env("HERMES_CONSOLE_SECRET") or secrets.token_urlsafe(48)
    )
    session_ttl_minutes: int = field(
        default_factory=lambda: int(_env("HERMES_CONSOLE_SESSION_TTL", "720"))
    )
    idle_ttl_minutes: int = field(
        default_factory=lambda: int(_env("HERMES_CONSOLE_IDLE_TTL", "60"))
    )
    secure_cookies: bool = field(
        default_factory=lambda: _env("HERMES_CONSOLE_SECURE_COOKIES", "0") in ("1", "true", "yes")
    )
    allowed_ips: str = field(default_factory=lambda: _env("HERMES_CONSOLE_ALLOWED_IPS"))
    require_2fa_admin: bool = field(
        default_factory=lambda: _env("HERMES_CONSOLE_REQUIRE_2FA", "0") in ("1", "true", "yes")
    )
    hermes_home: str = field(default_factory=lambda: _env("HERMES_HOME"))
    hermes_bin: str = field(default_factory=lambda: _env("HERMES_BIN"))

    @property
    def secret_key_persistent(self) -> bool:
        """密钥是否来自环境（持久）；否则为进程内随机，重启后所有会话失效。"""
        return bool(_env("HERMES_CONSOLE_SECRET"))

    @property
    def data_dir(self) -> Path:
        d = Path(_env("HERMES_CONSOLE_DATA") or
                 str(Path(__file__).resolve().parents[2] / "data"))
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return self.data_dir / "console.db"

    @property
    def jobs_dir(self) -> Path:
        d = self.data_dir / "jobs"
        d.mkdir(parents=True, exist_ok=True)
        return d


settings = Settings()
