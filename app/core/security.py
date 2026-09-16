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
# 静态加密（TOTP 种子等敏感配置项落库前加密）
# ---------------------------------------------------------------------------
#
# 设计：密钥从 HERMES_CONSOLE_SECRET 派生；构造为 HMAC-CTR 加密 + 加密后 MAC
# （encrypt-then-MAC，纯 stdlib，不引入 cryptography）。只防「DB 文件泄露即裸奔」，
# 不防拿到环境变量密钥的攻击者。未设置固定密钥时降级为明文存储，
# 避免随机密钥重启后把数据锁死（启动时会有显著警告）。

_ENC_PREFIX = "enc:v1:"


def _derived_key(purpose: str) -> bytes:
    return hmac.new(settings.secret_key.encode(),
                    ("at-rest:" + purpose).encode(), hashlib.sha256).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    blocks = (length + 31) // 32
    return b"".join(
        hmac.new(key, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
        for i in range(blocks)
    )[:length]


def encrypt_text(plain: str) -> str:
    """加密短字符串；空值/未设固定密钥时原样返回，已加密的不重复加密。"""
    if not plain or plain.startswith(_ENC_PREFIX) or not settings.secret_key_persistent:
        return plain
    nonce = secrets.token_bytes(16)
    data = plain.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(data, _keystream(_derived_key("enc"), nonce, len(data))))
    tag = hmac.new(_derived_key("mac"), nonce + ct, hashlib.sha256).digest()[:16]
    return _ENC_PREFIX + base64.urlsafe_b64encode(nonce + tag + ct).decode()


def decrypt_text(stored: str) -> str:
    """解密；非密文直接透传。MAC 校验失败（密钥换过/数据篡改）返回空串，
    验证侧 fail-closed，同时不让登录页因解密异常而 500。"""
    if not stored or not stored.startswith(_ENC_PREFIX):
        return stored
    try:
        raw = base64.urlsafe_b64decode(stored[len(_ENC_PREFIX):])
        nonce, tag, ct = raw[:16], raw[16:32], raw[32:]
        expected = hmac.new(_derived_key("mac"), nonce + ct, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(expected, tag):
            return ""
        pt = bytes(a ^ b for a, b in zip(ct, _keystream(_derived_key("enc"), nonce, len(ct))))
        return pt.decode("utf-8")
    except Exception:
        return ""


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
