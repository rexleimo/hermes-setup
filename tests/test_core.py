"""core 模块单测：密码/令牌/会话/限流。"""
from __future__ import annotations

import time

import pyotp

from app.core import ratelimit, sessions as session_store
from app.core.csrf import csrf_token_for, validate
from app.core.security import (
    hash_password, new_totp_secret, password_issues, sign_token,
    unsign_token, verify_password, verify_totp,
)


def test_password_hash_roundtrip():
    h = hash_password("Sup3rSecure!x")
    assert verify_password("Sup3rSecure!x", h)
    assert not verify_password("wrong", h)


def test_password_policy():
    assert password_issues("short")            # 太短
    assert password_issues("12345678901")      # 无字母
    assert password_issues("onlyletters")      # 无数字
    assert password_issues("GoodPass123") == []


def test_sign_token_roundtrip():
    token = sign_token("csrf:s1")
    assert unsign_token(token) == "csrf:s1"
    assert unsign_token(token + "x") is None
    assert unsign_token("tampered.0" * 2) is None


def test_csrf_validation():
    assert validate("sess-1", csrf_token_for("sess-1"))
    assert not validate("sess-1", csrf_token_for("sess-2"))
    assert not validate("sess-1", None)


def test_totp():
    secret = new_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert verify_totp(secret, code)
    assert not verify_totp(secret, "000000") or pyotp.TOTP(secret).verify("000000")


def test_session_lifecycle():
    s = session_store.create(None, ip="127.0.0.1", user_agent="pytest")
    got = session_store.get(s.id)
    assert got is not None and not got.is_authenticated
    session_store.mark_authenticated(s.id, 1, two_fa_ok=True)
    got = session_store.get(s.id)
    assert got.is_authenticated and got.two_fa_ok
    session_store.destroy(s.id)
    assert session_store.get(s.id) is None


def test_login_lockout_after_fails():
    ratelimit._local = None  # no-op,保持可读
    for i in range(5):
        ratelimit.record("10.9.9.9", "victim", success=False)
    verdict = ratelimit.check("10.9.9.9", "victim")
    assert not verdict.allowed
    # 其他账号不受影响（同 IP 但未触发 IP 上限）
    assert ratelimit.check("10.9.9.9", "other").allowed


def test_csrf_rejects_wrong_session():
    assert not validate("a", csrf_token_for("b"))
    assert time.time() > 0


def test_username_validation(clean_db):
    from app.core.appsettings import UserError, create_user

    import pytest

    # 合法：含连字符 / 点 / 下划线 / @
    for name in ("op-test", "a.b", "user_1", "me@x"):
        try:
            create_user(name, "ValidPass123", "operator")
        except UserError as e:
            if "已存在" not in str(e):
                raise
    # 非法：空格与符号
    for name in ("bad name", "bad$name", "名字"):
        with pytest.raises(UserError):
            create_user(name, "ValidPass123", "operator")
