"""登录限流：基于 DB 的滑动窗口计数 + 账号锁定。

策略（可按需收紧）：
- 同一 IP 15 分钟内最多 20 次尝试；
- 同一用户名连续失败 5 次后锁定 30 分钟（无论来源 IP，防撞库）。
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core import db

WINDOW_MINUTES = 15
IP_MAX_ATTEMPTS = 20
FAILS_BEFORE_LOCK = 5
LOCK_MINUTES = 30


@dataclass
class ThrottleVerdict:
    allowed: bool
    reason: str = ""


def record(ip: str, username: str, success: bool) -> None:
    db.execute(
        "INSERT INTO login_attempts (ip, username, success) VALUES (?,?,?)",
        (ip, username, int(success)),
    )
    db.execute(
        "DELETE FROM login_attempts WHERE ts < datetime('now','localtime','-2 days')"
    )


def check(ip: str, username: str) -> ThrottleVerdict:
    row = db.query_one(
        """SELECT COUNT(*) AS n FROM login_attempts
           WHERE ip = ? AND success = 0 AND ts > datetime('now','localtime', ?)""",
        (ip, f"-{WINDOW_MINUTES} minutes"),
    )
    if row and row["n"] >= IP_MAX_ATTEMPTS:
        return ThrottleVerdict(False, "该来源 IP 失败次数过多，请稍后再试")

    row = db.query_one(
        """SELECT MAX(ts) AS last_fail, SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS fails
           FROM login_attempts
           WHERE username = ? AND success = 0
             AND ts > datetime('now','localtime', ?)""",
        (username, f"-{LOCK_MINUTES} minutes"),
    )
    if row and (row["fails"] or 0) >= FAILS_BEFORE_LOCK:
        return ThrottleVerdict(
            False, f"账号已临时锁定（连续失败 {row['fails']} 次），请 {LOCK_MINUTES} 分钟后再试"
        )
    return ThrottleVerdict(True)
