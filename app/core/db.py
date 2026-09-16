"""SQLite（stdlib）数据访问层。

- WAL 模式，兼容管理后台的「少量并发写 + 频繁读」；
- 统一在此建表，领域模块只做读写；
- 行以 sqlite3.Row 返回，避免裸 tuple。
"""
from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from app.core.settings import settings

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'operator',   -- admin | operator
    active        INTEGER NOT NULL DEFAULT 1,
    totp_secret   TEXT,
    totp_enabled  INTEGER NOT NULL DEFAULT 0,
    must_change   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER,                -- NULL = 匿名会话（登录页）
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at   TEXT NOT NULL,
    ip           TEXT,
    user_agent   TEXT,
    two_fa_ok    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL DEFAULT (datetime('now')),
    username TEXT,
    action   TEXT NOT NULL,
    target   TEXT,
    detail   TEXT,
    ip       TEXT,
    outcome  TEXT NOT NULL DEFAULT 'ok'   -- ok | denied | failed
);

CREATE TABLE IF NOT EXISTS login_attempts (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL DEFAULT (datetime('now')),
    ip       TEXT,
    username TEXT,
    success  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_meta (
    id            TEXT PRIMARY KEY,   -- custom: providers.<id> 键名；preset: 预设 id
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL,      -- preset | custom
    preset_id     TEXT,
    protocol      TEXT NOT NULL,
    env_key       TEXT NOT NULL,
    base_url      TEXT DEFAULT '',
    default_model TEXT DEFAULT '',
    note          TEXT DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS provider_models (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id    TEXT NOT NULL,
    model_id       TEXT NOT NULL,
    display_name   TEXT,
    context_length INTEGER,
    enabled        INTEGER NOT NULL DEFAULT 1,
    note           TEXT,
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (provider_id, model_id)
);

CREATE TABLE IF NOT EXISTS mcp_meta (
    name       TEXT PRIMARY KEY,   -- mcp_servers.<name> 键名
    note       TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fallback_chain (
    position  INTEGER PRIMARY KEY,
    provider  TEXT NOT NULL,
    model     TEXT NOT NULL,
    base_url  TEXT,
    key_env   TEXT
);

CREATE TABLE IF NOT EXISTS job_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,            -- install | update | gateway_*
    command    TEXT,
    status     TEXT NOT NULL DEFAULT 'running',  -- running | ok | failed
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    exit_code  INTEGER,
    log_path   TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_attempts_ts ON login_attempts (ts);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    """每线程一个连接；写操作自动提交。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    yield conn
    conn.commit()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with db() as conn:
        return conn.execute(sql, params).fetchall()


def query_one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute(sql, params).fetchone()


def execute(sql: str, params: tuple = ()) -> int:
    with db() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid or cur.rowcount
