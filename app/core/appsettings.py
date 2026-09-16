"""平台账户与运行时设置（DB 可编辑部分）。"""
from __future__ import annotations

from app.core import db
from app.core.security import (
    decrypt_text, encrypt_text, hash_password, password_issues,
)
from app.core.settings import settings

# ---------------------------------------------------------------------------
# 运行时设置（key-value，JSON 字符串存储）
# ---------------------------------------------------------------------------

DEFAULTS: dict[str, str] = {
    # Hermes 相关路径（空 = 自动探测）
    "hermes_home": "",
    "hermes_bin": "",
    "gateway_log_lines": "200",
}


def get_setting(key: str) -> str:
    row = db.query_one("SELECT value FROM app_settings WHERE key = ?", (key,))
    if row is not None:
        return row["value"]
    if key == "hermes_home":
        return settings.hermes_home
    if key == "hermes_bin":
        return settings.hermes_bin
    return DEFAULTS.get(key, "")


def set_setting(key: str, value: str) -> None:
    db.execute(
        "INSERT INTO app_settings (key, value) VALUES (?,?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def all_settings() -> dict[str, str]:
    return {k: get_setting(k) for k in DEFAULTS}


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------


class UserError(Exception):
    pass


def _unwrap_user(row):
    """sqlite3.Row → dict，并解密 totp_secret。调用方一律用本模块接口拿用户。"""
    if row is None:
        return None
    data = dict(row)
    if data.get("totp_secret"):
        data["totp_secret"] = decrypt_text(data["totp_secret"])
    return data


def count_users() -> int:
    return db.query_one("SELECT COUNT(*) AS n FROM users")["n"]


def get_by_username(username: str):
    return _unwrap_user(db.query_one("SELECT * FROM users WHERE username = ?", (username,)))


def get(user_id: int):
    return _unwrap_user(db.query_one("SELECT * FROM users WHERE id = ?", (user_id,)))


def list_users() -> list:
    return [_unwrap_user(r) for r in db.query("SELECT * FROM users ORDER BY id")]


def create_user(username: str, password: str, role: str = "operator") -> int:
    username = username.strip()
    allowed_specials = set("._-@")
    if any(not (c.isalnum() or c in allowed_specials) for c in username):
        raise UserError("用户名仅允许字母、数字与 . _ - @")
    if len(username) < 3 or len(username) > 32:
        raise UserError("用户名长度需在 3-32 位之间")
    if get_by_username(username):
        raise UserError("用户名已存在")
    if role not in ("admin", "operator"):
        raise UserError("角色不合法")
    issues = password_issues(password)
    if issues:
        raise UserError("密码强度不足：" + "；".join(issues))
    return db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?,?,?)",
        (username, hash_password(password), role),
    )


def set_password(user_id: int, password: str) -> None:
    issues = password_issues(password)
    if issues:
        raise UserError("密码强度不足：" + "；".join(issues))
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
               (hash_password(password), user_id))


def set_role(user_id: int, role: str) -> None:
    if role not in ("admin", "operator"):
        raise UserError("角色不合法")
    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def set_active(user_id: int, active: bool) -> None:
    db.execute("UPDATE users SET active = ? WHERE id = ?", (int(active), user_id))


def set_totp(user_id: int, secret: str | None, enabled: bool) -> None:
    db.execute(
        "UPDATE users SET totp_secret = ?, totp_enabled = ? WHERE id = ?",
        (encrypt_text(secret) if secret else None, int(enabled), user_id),
    )


def touch_last_login(user_id: int) -> None:
    db.execute("UPDATE users SET last_login_at = datetime('now') WHERE id = ?",
               (user_id,))


def last_admin_active_count() -> int:
    return db.query_one(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1"
    )["n"]
