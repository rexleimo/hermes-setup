"""周期性 DB 卫生：过期会话清理、限流表修剪、每日快照。

不做调度库依赖：main.py 的 lifespan 里挂一个 asyncio 循环即可。
"""
from __future__ import annotations

import asyncio
import logging

from app.core import backup, db, sessions as session_store

log = logging.getLogger("hermes_console.maintenance")

INTERVAL_SECONDS = 15 * 60


def run_once() -> None:
    """一次清扫；任何子项失败不影响其余（各自吞异常记日志）。"""
    try:
        session_store.purge_expired()
    except Exception:  # pragma: no cover
        log.exception("purge_expired 失败")
    try:
        # ratelimit.record 也会顺手清，这里兜底长期无人登录的情况
        db.execute("DELETE FROM login_attempts WHERE ts < datetime('now','-2 days')")
    except Exception:  # pragma: no cover
        log.exception("login_attempts 清理失败")
    try:
        backup.run_due_backup()
    except Exception:  # pragma: no cover
        log.exception("DB 快照失败")


async def maintenance_loop(interval: int = INTERVAL_SECONDS) -> None:
    """常驻后台任务：启动时先跑一次，之后每 interval 秒一次。"""
    while True:
        run_once()
        await asyncio.sleep(interval)
