"""服务端会话：DB 存储，支持匿名（登录前）与已认证两种形态。

Cookie 仅存放 session id；真实状态在服务端，可随时吊销。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core import db
from app.core.security import new_session_id
from app.core.settings import settings

COOKIE_NAME = "hermes_console_session"


@dataclass
class Session:
    id: str
    user_id: int | None
    two_fa_ok: bool
    ip: str | None
    user_agent: str | None

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None


def _now() -> datetime:
    return datetime.now()


def create(user_id: int | None, ip: str = "", user_agent: str = "") -> Session:
    sid = new_session_id()
    expires = _now() + timedelta(minutes=settings.session_ttl_minutes)
    db.execute(
        "INSERT INTO sessions (id, user_id, expires_at, ip, user_agent) VALUES (?,?,?,?,?)",
        (sid, user_id, expires.strftime("%Y-%m-%d %H:%M:%S"), ip, user_agent[:256]),
    )
    return Session(id=sid, user_id=user_id, two_fa_ok=False, ip=ip, user_agent=user_agent)


def get(sid: str) -> Session | None:
    """读取会话并滑动续期（空闲过期）；匿名会话不续期。"""
    if not sid:
        return None
    row = db.query_one("SELECT * FROM sessions WHERE id = ?", (sid,))
    if row is None:
        return None
    expires = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S")
    last_seen = datetime.strptime(row["last_seen_at"], "%Y-%m-%d %H:%M:%S")
    now = _now()
    if now > expires:
        destroy(sid)
        return None
    if row["user_id"] is not None and now - last_seen > timedelta(minutes=settings.idle_ttl_minutes):
        destroy(sid)
        return None

    if row["user_id"] is not None:
        db.execute(
            "UPDATE sessions SET last_seen_at = datetime('now','localtime') WHERE id = ?",
            (sid,),
        )
        # 绝对过期时间随活跃顺延（不超过 session_ttl 的语义由 expires_at 兜底）
        new_exp = min(
            expires,
            now + timedelta(minutes=settings.session_ttl_minutes),
            now + timedelta(minutes=max(settings.idle_ttl_minutes, 1) * 12),
        )
        db.execute("UPDATE sessions SET expires_at = ? WHERE id = ?",
                   (new_exp.strftime("%Y-%m-%d %H:%M:%S"), sid))

    return Session(
        id=sid,
        user_id=row["user_id"],
        two_fa_ok=bool(row["two_fa_ok"]),
        ip=row["ip"],
        user_agent=row["user_agent"],
    )


def mark_authenticated(sid: str, user_id: int, two_fa_ok: bool) -> None:
    db.execute(
        "UPDATE sessions SET user_id = ?, two_fa_ok = ?, ip = COALESCE(ip, '') WHERE id = ?",
        (user_id, int(two_fa_ok), sid),
    )


def mark_two_fa(sid: str) -> None:
    db.execute("UPDATE sessions SET two_fa_ok = 1 WHERE id = ?", (sid,))


def destroy(sid: str) -> None:
    db.execute("DELETE FROM sessions WHERE id = ?", (sid,))


def destroy_user_sessions(user_id: int) -> None:
    db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def purge_expired() -> None:
    db.execute("DELETE FROM sessions WHERE expires_at < datetime('now','localtime')")
