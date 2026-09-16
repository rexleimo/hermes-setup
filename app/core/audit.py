"""审计日志：所有变更操作（含被拒绝的）统一留痕，仅追加、不可通过 UI 删除。"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from app.core import db


def record(
    action: str,
    *,
    username: str | None = None,
    target: str | None = None,
    detail: dict | str | None = None,
    ip: str | None = None,
    outcome: str = "ok",
) -> None:
    if isinstance(detail, (dict, list)):
        try:
            detail = json.dumps(detail, ensure_ascii=False)
        except TypeError:
            detail = str(detail)
    db.execute(
        "INSERT INTO audit_log (username, action, target, detail, ip, outcome) VALUES (?,?,?,?,?,?)",
        (username, action, target, detail, ip, outcome),
    )


@dataclass
class AuditPage:
    rows: list[sqlite3.Row]
    total: int
    page: int
    pages: int


def page(*, p: int = 1, per_page: int = 30, action: str = "", username: str = "",
         outcome: str = "") -> AuditPage:
    where, params = [], []
    if action:
        where.append("action LIKE ?")
        params.append(f"{action}%")
    if username:
        where.append("username LIKE ?")
        params.append(f"%{username}%")
    if outcome:
        where.append("outcome = ?")
        params.append(outcome)
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    total = db.query_one(f"SELECT COUNT(*) AS n FROM audit_log {clause}", tuple(params))["n"]
    pages = max(1, -(-total // per_page))
    p = max(1, min(p, pages))
    rows = db.query(
        f"SELECT * FROM audit_log {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params) + (per_page, (p - 1) * per_page),
    )
    return AuditPage(rows=rows, total=total, page=p, pages=pages)


def recent(n: int = 8) -> list[sqlite3.Row]:
    return db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (n,))
