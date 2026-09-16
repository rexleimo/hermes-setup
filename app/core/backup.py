"""平台 DB 每日快照：SQLite 在线备份 API，保留最近 N 份轮转。

约定（与 config.yaml 备份同一哲学）：
- 目标目录 data/backups/console-<UTC时间戳>.db；
- 超过 KEEP 份自动删最旧；距上次成功备份不足一天则跳过；
- 用 stdlib sqlite3.Connection.backup()，WAL 下也是一致性快照，不复制半成品。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.core.settings import settings

log = logging.getLogger("hermes_console.backup")

KEEP = 5
DAILY_INTERVAL_SECONDS = 24 * 3600


def backups_dir() -> Path:
    d = settings.data_dir / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_backups() -> list[Path]:
    return sorted(backups_dir().glob("console-*.db"), reverse=True)


def create_backup() -> Path | None:
    """立即生成一份快照并轮转；DB 文件不存在（尚未初始化）时返回 None。"""
    src_path = settings.db_path
    if not Path(src_path).exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dst_path = backups_dir() / f"console-{stamp}.db"
    with sqlite3.connect(src_path, timeout=15) as src, \
         sqlite3.connect(dst_path) as dst:
        src.backup(dst)
    for stale in list_backups()[KEEP:]:
        stale.unlink(missing_ok=True)
    log.info("已生成 DB 快照：%s（保留最近 %d 份）", dst_path.name, KEEP)
    return dst_path


def run_due_backup() -> Path | None:
    """距最近一份快照超过一天才备份 —— 由维护循环/启动钩子调用，幂等。"""
    latest = list_backups()
    if latest:
        age = datetime.now(timezone.utc).timestamp() - latest[0].stat().st_mtime
        if age < DAILY_INTERVAL_SECONDS:
            return None
    return create_backup()
