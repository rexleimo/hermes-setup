"""用户管理（仅管理员）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from app.core import audit, sessions as session_store
from app.core.appsettings import (
    UserError, create_user, get, last_admin_active_count, list_users,
    set_active, set_password, set_role, set_totp,
)
from app.core.security import password_issues
from app.web.deps import Admin, client_ip, csrf_guard
from app.web.htmx import redirect, toast
from app.web.templating import render

router = APIRouter(prefix="/users", dependencies=[Depends(csrf_guard)])


@router.get("")
def users_page(request: Request, admin: Admin):
    return render(request, "users.html", {
        "nav_active": "platform",
        "tab": "users",
        "users": list_users(),
    })


@router.post("")
def create(request: Request, admin: Admin, username: str = Form(...),
           password: str = Form(...), role: str = Form("operator")):
    try:
        new_id = create_user(username, password, role)
    except UserError as exc:
        return _reject(request, str(exc))
    audit.record("user_create", username=admin["username"], target=username.strip(),
                 detail={"role": role}, ip=client_ip(request))
    resp = redirect(request, "/users")
    toast(resp, f"用户 {username.strip()} 已创建")
    return resp


@router.post("/{user_id}/action")
def action(request: Request, admin: Admin, user_id: int,
           do: str = Form(...), password: str = Form(""), role: str = Form("")):
    target = get(user_id)
    if target is None:
        return _reject(request, "用户不存在", 404)

    # 保护：不允许把唯一启用的管理员降级/停用
    if target["role"] == "admin" and do in ("disable", "set_role") :
        if role == "operator" or do == "disable":
            if last_admin_active_count() <= 1:
                return _reject(request, "至少保留一名启用的管理员")

    message = ""
    try:
        if do == "disable":
            set_active(user_id, False)
            session_store.destroy_user_sessions(user_id)
            message = f"用户 {target['username']} 已停用并强制下线"
            audit.record("user_disable", username=admin["username"], target=target["username"],
                         ip=client_ip(request))
        elif do == "enable":
            set_active(user_id, True)
            message = f"用户 {target['username']} 已启用"
            audit.record("user_enable", username=admin["username"], target=target["username"],
                         ip=client_ip(request))
        elif do == "reset_password":
            issues = password_issues(password)
            if issues:
                return _reject(request, "密码强度不足：" + "；".join(issues))
            set_password(user_id, password)
            session_store.destroy_user_sessions(user_id)
            message = f"用户 {target['username']} 的密码已重置（所有会话已下线）"
            audit.record("user_reset_password", username=admin["username"],
                         target=target["username"], ip=client_ip(request))
        elif do == "set_role":
            set_role(user_id, role)
            message = f"用户 {target['username']} 的角色已调整为 {role}"
            audit.record("user_set_role", username=admin["username"],
                         target=target["username"], detail={"role": role},
                         ip=client_ip(request))
        elif do == "reset_2fa":
            set_totp(user_id, None, False)
            message = f"用户 {target['username']} 的两步验证已重置"
            audit.record("user_reset_2fa", username=admin["username"],
                         target=target["username"], ip=client_ip(request))
        else:
            return _reject(request, "未知操作")
    except UserError as exc:
        return _reject(request, str(exc))

    resp = redirect(request, "/users")
    toast(resp, message)
    return resp


def _reject(request: Request, message: str, code: int = 400):
    from fastapi.responses import HTMLResponse

    from app.web.htmx import is_htmx

    if is_htmx(request):
        return HTMLResponse(f"<div class='alert alert-danger'>{message}</div>", status_code=code)
    return render(request, "error.html",
                  {"nav_active": "platform",
        "tab": "users", "code": code, "message": message},
                  status_code=code)
