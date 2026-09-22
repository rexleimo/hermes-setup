"""服务端会话：DB 存储，支持匿名（登录前）与已认证两种形态。

Cookie 仅存放 session id；真实状态在服务端，可随时吊销。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core import db
from app.core.security import new_session_id
from app.core.settings import settings

COOKIE_NAME = "hermes_console_session"
# 滑动续期落库的最小间隔：活跃会话最多每 N 秒一次 UPDATE，而不是每个请求两条。
_SLIDE_SECONDS = 60


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
    """naive UTC：与库内 datetime('now')（UTC）同基准，避免时区/夏令时跳变。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create(user_id: int | None, ip: str = "", user_agent: str = "") -> Session:
    sid = new_session_id()
    expires = _now() + timedelta(minutes=settings.session_ttl_minutes)
    db.execute(
        "INSERT INTO sessions (id, user_id, expires_at, ip, user_agent) VALUES (?,?,?,?,?)",
        (sid, user_id, expires.strftime("%Y-%m-%d %H:%M:%S"), ip, user_agent[:256]),
    )
    return Session(id=sid, user_id=user_id, two_fa_ok=False, ip=ip, user_agent=user_agent)


def _slide(sid: str, expires: datetime, now: datetime) -> None:
    """滑动续期：last_seen 与绝对过期一次写完（两条 UPDATE = 两个写事务）。"""
    new_exp = min(
        expires,
        now + timedelta(minutes=settings.session_ttl_minutes),
        now + timedelta(minutes=max(settings.idle_ttl_minutes, 1) * 12),
    )
    db.execute(
        "UPDATE sessions SET last_seen_at = datetime('now'), expires_at = ? WHERE id = ?",
        (new_exp.strftime("%Y-%m-%d %H:%M:%S"), sid),
    )


def get(sid: str) -> Session | None:
    """读取会话并按需滑动续期（空闲过期）；匆名会话不续期。

    续期写入限流到每 _SLIDE_SECONDS 一次：/files 网格的每个缩略图都是一个带
    cookie 的请求，以前每请求两条 UPDATE —— 一个照片页 = 几百次写事务在
    SQLite 写锁上串行，是线上文件管理器打不开的直接原因之一。
    """
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
    if row["user_id"] is None:
        return Session(id=sid, user_id=None, two_fa_ok=bool(row["two_fa_ok"]),
                       ip=row["ip"], user_agent=row["user_agent"])
    if now - last_seen > timedelta(minutes=settings.idle_ttl_minutes):
        destroy(sid)
        return None
    if now - last_seen > timedelta(seconds=_SLIDE_SECONDS):
        _slide(sid, expires, now)

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
    db.execute("DELETE FROM sessions WHERE expires_at < datetime('now')")
