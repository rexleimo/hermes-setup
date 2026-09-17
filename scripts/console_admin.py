#!/usr/bin/env python3
"""Hermes Console 服务器端紧急工具（不依赖 Web 登录）。

用法（在部署机上，仓库根目录或有 app 包的路径下）：

    python scripts/console_admin.py list-users
    python scripts/console_admin.py reset-password <username>
    python scripts/console_admin.py disable-2fa <username>
    python scripts/console_admin.py list-sessions
    python scripts/console_admin.py revoke-session <session-id>
    python scripts/console_admin.py create-admin <username>   # 仅当库中无任何用户

数据库路径遵循 HERMES_CONSOLE_DATA（默认：旧部署的项目内 data/，新安装的
~/.hermes-console）。所有动作打印审计友好摘要。
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import db, sessions as session_store  # noqa: E402
from app.core.appsettings import (  # noqa: E402
    UserError, count_users, create_user, get_by_username, list_users,
    set_password, set_totp,
)


def _table(rows: list[dict], cols: list[str]) -> None:
    if not rows:
        print("(空)")
        return
    widths = [max(len(c), *(len(str(r.get(c) if r.get(c) is not None else "-")) for r in rows))
              for c in cols]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(str(r.get(c, "") if r.get(c) is not None else "-").ljust(w)
                        for c, w in zip(cols, widths)))


def cmd_list_users(_args) -> int:
    _table([dict(u) for u in list_users()],
           ["id", "username", "role", "active", "totp_enabled", "last_login_at"])
    return 0


def cmd_reset_password(args) -> int:
    user = get_by_username(args.username)
    if user is None:
        print(f"用户不存在：{args.username}", file=sys.stderr)
        return 2
    pw1 = getpass.getpass(f"为 {args.username} 设置新密码: ")
    pw2 = getpass.getpass("再次输入: ")
    if pw1 != pw2:
        print("两次输入不一致", file=sys.stderr)
        return 2
    try:
        set_password(user["id"], pw1)
    except UserError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    # 与 Web 端改密一致：吊销该用户所有会话，强制重新登录
    session_store.destroy_user_sessions(user["id"])
    db.execute("INSERT INTO audit_log (username, action, detail, outcome) "
               "VALUES (?, 'admin_cli_reset_password', ?, 'ok')",
               (f"cli:{getpass.getuser()}", f"target={args.username}"))
    print(f"已重置 {args.username} 的密码并吊销其全部会话")
    return 0


def cmd_disable_2fa(args) -> int:
    user = get_by_username(args.username)
    if user is None:
        print(f"用户不存在：{args.username}", file=sys.stderr)
        return 2
    set_totp(user["id"], None, enabled=False)
    session_store.destroy_user_sessions(user["id"])
    db.execute("INSERT INTO audit_log (username, action, detail, outcome) "
               "VALUES (?, 'admin_cli_disable_2fa', ?, 'ok')",
               (f"cli:{getpass.getuser()}", f"target={args.username}"))
    print(f"已关闭 {args.username} 的两步验证（其会话已全部吊销）")
    return 0


def cmd_list_sessions(_args) -> int:
    rows = db.query(
        "SELECT s.id, s.user_id, u.username, s.ip, s.last_seen_at, s.expires_at "
        "FROM sessions s LEFT JOIN users u ON u.id = s.user_id "
        "ORDER BY s.last_seen_at DESC LIMIT 100")
    _table([dict(r) for r in rows],
           ["id", "username", "ip", "last_seen_at", "expires_at"])
    return 0


def cmd_revoke_session(args) -> int:
    if session_store.get(args.session_id) is None:
        print("会话不存在或已过期", file=sys.stderr)
        return 2
    session_store.destroy(args.session_id)
    print(f"已吊销会话 {args.session_id[:12]}…")
    return 0


def cmd_create_admin(args) -> int:
    if count_users() > 0:
        print("库中已有用户，拒绝创建（防越权提权）；请用 reset-password", file=sys.stderr)
        return 2
    pw = getpass.getpass(f"为 {args.username} 设置密码: ")
    try:
        create_user(args.username, pw, role="admin")
    except UserError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"已创建管理员 {args.username}（请记录 HERMES_CONSOLE_SECRET 是否已设置）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list-users").set_defaults(fn=cmd_list_users)
    sub.add_parser("list-sessions").set_defaults(fn=cmd_list_sessions)
    p = sub.add_parser("reset-password"); p.add_argument("username"); p.set_defaults(fn=cmd_reset_password)
    p = sub.add_parser("disable-2fa"); p.add_argument("username"); p.set_defaults(fn=cmd_disable_2fa)
    p = sub.add_parser("revoke-session"); p.add_argument("session_id"); p.set_defaults(fn=cmd_revoke_session)
    p = sub.add_parser("create-admin"); p.add_argument("username"); p.set_defaults(fn=cmd_create_admin)

    db.init_db()
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
