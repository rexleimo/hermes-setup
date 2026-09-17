"""系统设置（管理员）与个人账户设置。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request

from app.core import audit, backup, db, sessions as session_store
from app.core.appsettings import UserError, get_by_username, set_password, set_setting
from app.core.backup import BackupError
from app.core.security import verify_password
from app.hermes.paths import detect
from app.web.deps import Admin, User, client_ip, csrf_guard, get_session
from app.web.htmx import redirect, toast
from app.web.templating import render, render_partial

router = APIRouter(prefix="/settings", dependencies=[Depends(csrf_guard)])


@router.get("")
def settings_page(request: Request, admin: Admin):
    from app.core.appsettings import all_settings
    from app.core.settings import settings as env_settings

    paths = detect()
    return render(request, "settings.html", {
        "nav_active": "platform",
        "tab": "settings",
        "settings": all_settings(),
        "paths": paths,
        "ip_allowlist": env_settings.allowed_ips,
        "session_ttl": env_settings.session_ttl_minutes,
        "idle_ttl": env_settings.idle_ttl_minutes,
        "secret_persistent": env_settings.secret_key_persistent,
        "console_data_dir": env_settings.data_dir,
        "backups": backup.list_backups()[:8],
    })


@router.post("")
def settings_save(request: Request, admin: Admin,
                  hermes_home: str = Form(""), hermes_bin: str = Form(""),
                  gateway_log_lines: str = Form("200")):
    set_setting("hermes_home", hermes_home.strip())
    set_setting("hermes_bin", hermes_bin.strip())
    if gateway_log_lines.strip().isdigit():
        set_setting("gateway_log_lines", gateway_log_lines.strip())
    audit.record("settings_save", username=admin["username"],
                 detail={"hermes_home": hermes_home.strip(),
                         "hermes_bin": hermes_bin.strip()},
                 ip=client_ip(request))
    resp = redirect(request, "/settings")
    toast(resp, "系统设置已保存")
    return resp


@router.get("/probe")
def probe_fragment(request: Request, admin: Admin):
    paths = detect()
    return render_partial(request, "settings/_probe.html", {"paths": paths})


@router.post("/backup")
def backup_now(request: Request, admin: Admin):
    made = backup.create_backup()
    audit.record("db_backup", username=admin["username"],
                 detail=str(made) if made else "无 DB 文件", ip=client_ip(request))
    resp = redirect(request, "/settings")
    toast(resp, "已生成 DB 快照" if made else "暂无可备份的数据库",
          level="success" if made else "error")
    return resp


@router.post("/backup/restore")
def backup_restore(request: Request, admin: Admin, name: str = Form(...)):
    """一键恢复指定快照。恢复前自动留一份当前快照；快照里的 sessions 会整体
    回滚导致其他会话被登出 —— 当前管理员的会话行在恢复后原样补回，避免自己被踢。"""
    ip = client_ip(request)
    sess = get_session(request)
    keep_row = db.query_one("SELECT * FROM sessions WHERE id = ?", (sess.id,)) \
        if sess else None

    try:
        safety = backup.restore_backup(name.strip())
    except BackupError as exc:
        audit.record("db_restore", username=admin["username"], outcome="failed",
                     detail=str(exc), ip=ip)
        resp = redirect(request, "/settings")
        toast(resp, f"恢复失败：{exc}", level="error")
        return resp

    if keep_row is not None:
        cols = ("id", "user_id", "created_at", "last_seen_at", "expires_at",
                "ip", "user_agent", "two_fa_ok")
        db.execute(
            f"INSERT OR REPLACE INTO sessions ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            tuple(keep_row[c] for c in cols))

    audit.record("db_restore", username=admin["username"],
                 detail=f"snapshot={name.strip()}; safety={Path(safety).name}",
                 ip=ip)
    resp = redirect(request, "/settings")
    toast(resp, "已恢复所选快照（其他登录会话已下线；恢复前状态已自动备份）")
    return resp


# ---------------------------------------------------------------------------
# 个人账户
# ---------------------------------------------------------------------------

@router.get("/account")
def account_page(request: Request, user: User):
    return render(request, "account.html", {"nav_active": "platform"})


@router.post("/account/password")
def change_password(request: Request, user: User,
                    old_password: str = Form(...), new_password: str = Form(...)):
    if not verify_password(old_password, user["password_hash"]):
        return _account_reject(request, "当前密码不正确")
    try:
        set_password(user["id"], new_password)
    except UserError as exc:
        return _account_reject(request, str(exc))
    # 改密后吊销该用户所有其他会话（保留当前会话以外的都清掉）
    session_store.destroy_user_sessions(user["id"])
    resp = redirect(request, "/settings/account")
    toast(resp, "密码已修改，其余会话已全部下线")
    return resp


def _account_reject(request: Request, message: str):
    from fastapi.responses import HTMLResponse

    from app.web.htmx import is_htmx

    if is_htmx(request):
        return HTMLResponse(f"<div class='alert alert-danger'>{message}</div>", status_code=400)
    return render(request, "account.html",
                  {"nav_active": "platform", "error": message}, status_code=400)
