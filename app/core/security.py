"""密码学相关原语：Argon2 密码哈希、TOTP 两步验证、HMAC 签名令牌。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import base64

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from app.core.settings import settings

_hasher = PasswordHasher()  # argon2id, 默认参数即可满足后台场景

# ---------------------------------------------------------------------------
# 密码
# ---------------------------------------------------------------------------

MIN_PASSWORD_LEN = 10


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_issues(plain: str) -> list[str]:
    """密码策略校验：返回问题列表（空列表 = 通过）。"""
    issues: list[str] = []
    if len(plain) < MIN_PASSWORD_LEN:
        issues.append(f"长度至少 {MIN_PASSWORD_LEN} 位")
    if not any(c.isdigit() for c in plain):
        issues.append("需包含数字")
    if not any(c.isalpha() for c in plain):
        issues.append("需包含字母")
    if plain.lower() in {"password", "12345678", "qwertyuiop", "admin12345"}:
        issues.append("过于常见")
    return issues


def need_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return False


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------

def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(
        name=username, issuer_name="Hermes Console"
    )


def totp_svg(secret: str, username: str) -> str:
    """otpauth 二维码的 inline SVG（无 PIL 依赖）。"""
    import io

    import qrcode.image.svg

    buf = io.BytesIO()
    qrcode.make(
        totp_uri(secret, username), image_factory=qrcode.image.svg.SvgPathImage, box_size=12
    ).save(buf)
    return buf.getvalue().decode()


def verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    return pyotp.TOTP(secret).verify(code.strip().replace(" ", ""), valid_window=1)


# ---------------------------------------------------------------------------
# HMAC 签名（CSRF 令牌等无状态凭证）
# ---------------------------------------------------------------------------

def _mac(payload: str) -> str:
    key = settings.secret_key.encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def sign_token(payload: str) -> str:
    """返回 `payload.mac` 形式的防篡改令牌。"""
    return f"{payload}.{_mac(payload)}"


def unsign_token(token: str) -> str | None:
    """校验签名并还原 payload；非法返回 None。"""
    if not token or token.count(".") < 1:
        return None
    payload, _, mac = token.rpartition(".")
    if not hmac.compare_digest(_mac(payload), mac):
        return None
    return payload


def new_session_id() -> str:
    return secrets.token_urlsafe(32)
