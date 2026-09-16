"""CSRF 防护：与会话绑定的 HMAC 令牌，双提交（表单字段 + 头部）均可校验。"""
from __future__ import annotations

import hmac
from hashlib import sha256

from app.core.security import sign_token, unsign_token


def csrf_token_for(session_id: str) -> str:
    return sign_token(f"csrf:{session_id}")


def validate(session_id: str, presented: str | None) -> bool:
    expected = csrf_token_for(session_id)
    if not presented:
        return False
    return hmac.compare_digest(expected, presented)


def session_id_from_token(token: str) -> str | None:
    payload = unsign_token(token)
    if payload and payload.startswith("csrf:"):
        return payload[5:]
    return None


# 需要校验 CSRF 的方法
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
