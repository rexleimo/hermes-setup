"""平台 DB 快照：在线备份与一键恢复。

约定（与 config.yaml 备份同一哲学）：
- 目标目录 data/backups/console-<UTC时间戳>.db；
- 超过 KEEP 份自动删最旧；距上次成功备份不足一天则跳过（手动备份/恢复不受限）；
- 备份与恢复都用 stdlib sqlite3.Connection.backup()：WAL 下也是一致性快照；
  恢复把快照灌回活动连接（而非替换文件），与线程级缓存连接、WAL 不产生半成品；
- 恢复前自动给当前状态留一份快照，改坏可回。
"""
from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.core import db
from app.core.settings import settings

log = logging.getLogger("hermes_console.backup")

KEEP = 5
DAILY_INTERVAL_SECONDS = 24 * 3600
_SNAPSHOT_NAME = re.compile(r"^console-\d{8}-\d{6}(?:-\d+)?\.db$")


class BackupError(Exception):
    pass


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
    n = 1
    while dst_path.exists():  # 同一秒多次备份（如恢复前的安全备份）：加序号，绝不覆盖既有快照
        dst_path = backups_dir() / f"console-{stamp}-{n}.db"
        n += 1
    # closing() 必须显式关连接：sqlite3 连接的 with 只管事务提交/回滚，
    # 不关句柄——Windows 上句柄不释放，轮转 unlink 旧快照就 WinError 32（实机踩过）
    with closing(sqlite3.connect(src_path, timeout=15)) as src, \
         closing(sqlite3.connect(dst_path)) as dst:
        src.backup(dst)
    for stale in list_backups()[KEEP:]:
        stale.unlink(missing_ok=True)
    log.info("已生成 DB 快照：%s（保留最近 %d 份）", dst_path.name, KEEP)
    return dst_path


def restore_backup(name: str) -> Path:
    """把指定快照恢复为当前数据库；返回恢复前的安全快照路径。

    安全护栏：只接受本目录内、命名合法的快照（防路径穿越）；
    恢复动作本身通过 backup API 写入当前连接，事务性完成。
    注意：快照里的 users/sessions 会整体回滚 —— 恢复后其他会话全部失效，
    调用方（路由）负责保留当前管理员的会话。
    """
    if not _SNAPSHOT_NAME.match(name):
        raise BackupError("快照文件名不合法")
    target = backups_dir() / name
    if not target.is_file():
        raise BackupError("快照不存在或已被清理")
    safety = create_backup()  # 先给当前状态留退路（即使当日已备份也强制一份）
    with db.db() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        src = sqlite3.connect(target, timeout=15)
        try:
            src.backup(conn)
        finally:
            src.close()
    log.warning("已从快照恢复数据库：%s（恢复前快照：%s）",
                name, safety.name if safety else "无")
    return safety if safety else target


def run_due_backup() -> Path | None:
    """距最近一份快照超过一天才备份 —— 由维护循环/启动钩子调用，幂等。"""
    latest = list_backups()
    if latest:
        age = datetime.now(timezone.utc).timestamp() - latest[0].stat().st_mtime
        if age < DAILY_INTERVAL_SECONDS:
            return None
    return create_backup()
