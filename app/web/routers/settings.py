"""系统设置（管理员）与个人账户设置。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from app.core import audit, sessions as session_store
from app.core.appsettings import UserError, get_by_username, set_password, set_setting
from app.core.security import verify_password
from app.hermes.paths import detect
from app.web.deps import Admin, User, client_ip, csrf_guard
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
