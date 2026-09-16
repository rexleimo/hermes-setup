"""认证路由：首次初始化向导、登录、TOTP 挑战与绑定、登出。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from app.core import audit, ratelimit, sessions as session_store
from app.core import appsettings as users
from app.core.security import (
    hash_password, new_totp_secret, totp_svg, verify_password, verify_totp,
)
from app.core.settings import settings
from app.web.deps import User, client_ip, csrf_guard, get_session_required
from app.web.htmx import redirect, toast
from app.web.templating import render

router = APIRouter(dependencies=[Depends(csrf_guard)])


def _safe_next(next_url: str | None) -> str:
    """只允许站内相对路径，防开放重定向。"""
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return "/"
    return next_url


def _needs_setup() -> bool:
    return users.count_users() == 0


# ---------------------------------------------------------------------------
# 首次初始化
# ---------------------------------------------------------------------------

@router.get("/setup")
def setup_page(request: Request):
    if not _needs_setup():
        return redirect(request, "/login")
    return render(request, "auth/setup.html", {"nav_active": ""})


@router.post("/setup")
def setup_submit(request: Request, username: str = Form(...), password: str = Form(...),
                 password2: str = Form(...)):
    if not _needs_setup():
        return redirect(request, "/login")
    ip = client_ip(request)
    try:
        if password != password2:
            raise users.UserError("两次输入的密码不一致")
        users.create_user(username, password, role="admin")
    except users.UserError as exc:
        audit.record("setup", outcome="failed", detail=str(exc), ip=ip)
        return render(request, "auth/setup.html", {"error": str(exc)}, status_code=400)
    audit.record("setup", username=username, detail="创建初始管理员", ip=ip)
    return redirect(request, "/login")


# ---------------------------------------------------------------------------
# 登录 / 登出
# ---------------------------------------------------------------------------

def _login_page(request: Request, error: str = "", next_url: str = "", status: int = 200,
                username: str = ""):
    return render(request, "auth/login.html",
                  {"error": error, "next": next_url, "username": username},
                  status_code=status)


@router.get("/login")
def login_page(request: Request, next: str = "", error: str = ""):
    if _needs_setup():
        return redirect(request, "/setup")
    user = users_get_current(request)
    if user:
        return redirect(request, "/")
    return _login_page(request, error=error, next_url=next)


def users_get_current(request: Request):
    from app.web.deps import current_user

    return current_user(request)


@router.post("/login")
def login_submit(request: Request, username: str = Form(""), password: str = Form(""),
                 next: str = Form("")):
    username = username.strip()
    ip = client_ip(request)
    target = _safe_next(next)

    verdict = ratelimit.check(ip, username)
    if not verdict.allowed:
        audit.record("login", username=username, outcome="denied",
                     detail=verdict.reason, ip=ip)
        return _login_page(request, error=verdict.reason, next_url=next,
                           status=429, username=username)

    user = users.get_by_username(username)
    if user is None or not user["active"] or not verify_password(password, user["password_hash"]):
        ratelimit.record(ip, username, success=False)
        audit.record("login", username=username, outcome="failed", ip=ip)
        return _login_page(request, error="用户名或密码不正确", next_url=next,
                           status=401, username=username)

    ratelimit.record(ip, username, success=True)

    sess = get_session_required(request) or session_store.create(
        user_id=None, ip=ip, user_agent=request.headers.get("user-agent", ""))
    two_fa_needed = bool(user["totp_enabled"])
    session_store.mark_authenticated(sess.id, user["id"], two_fa_ok=not two_fa_needed)
    users.touch_last_login(user["id"])
    audit.record("login", username=username, ip=ip)

    if two_fa_needed:
        return redirect(request, "/login/2fa")

    resp = redirect(request, target)
    _set_cookie(resp, sess.id, request)
    return resp


def _set_cookie(response, sid: str, request: Request) -> None:
    response.set_cookie(
        session_store.COOKIE_NAME, sid,
        max_age=settings.session_ttl_minutes * 60,
        httponly=True, samesite="strict",
        secure=settings.secure_cookies or request.url.scheme == "https",
        path="/",
    )


@router.get("/login/2fa")
def two_fa_page(request: Request, error: str = ""):
    sess = get_session_required(request)
    if sess is None or not sess.is_authenticated:
        return redirect(request, "/login")
    user = users.get(sess.user_id)
    if user is None or not user["totp_enabled"] or (sess and sess.two_fa_ok):
        return redirect(request, "/")
    return render(request, "auth/twofa.html", {"error": error},
                  status_code=401 if error else 200)


@router.post("/login/2fa")
def two_fa_submit(request: Request, code: str = Form(...)):
    sess = get_session_required(request)
    if sess is None or not sess.is_authenticated:
        return redirect(request, "/login")
    user = users.get(sess.user_id)
    if user is None or not user["totp_enabled"]:
        return redirect(request, "/")
    if not verify_totp(user["totp_secret"], code):
        audit.record("login_2fa", username=user["username"], outcome="failed",
                     ip=client_ip(request))
        return render(request, "auth/twofa.html",
                      {"error": "验证码不正确，请重试"}, status_code=401)
    session_store.mark_two_fa(sess.id)
    audit.record("login_2fa", username=user["username"], ip=client_ip(request))
    return redirect(request, "/")


@router.post("/logout")
def logout(request: Request):
    sess = get_session_required(request)
    if sess is not None:
        session_store.destroy(sess.id)
    resp = redirect(request, "/login")
    resp.delete_cookie(session_store.COOKIE_NAME, path="/")
    return resp


# ---------------------------------------------------------------------------
# 自助 TOTP 绑定 / 解绑
# ---------------------------------------------------------------------------

@router.get("/2fa/setup")
def twofa_setup(request: Request, user: User):
    secret = new_totp_secret()
    users.set_totp(user["id"], secret, enabled=False)
    return render(request, "auth/2fa_setup.html", {
        "secret": secret, "qr_svg": totp_svg(secret, user["username"]),
    })


@router.post("/2fa/enable")
def twofa_enable(request: Request, code: str = Form(...)):
    sess = get_session_required(request)
    u = users_get_current(request)
    if u is None or u["totp_secret"] is None:
        return redirect(request, "/")
    if not verify_totp(u["totp_secret"], code):
        return render(request, "auth/2fa_setup.html", {
            "secret": u["totp_secret"],
            "qr_svg": totp_svg(u["totp_secret"], u["username"]),
            "error": "验证码不正确，请重试",
        }, status_code=400)
    users.set_totp(u["id"], u["totp_secret"], enabled=True)
    if sess is not None:
        session_store.mark_two_fa(sess.id)
    # 安全细节：绑定后重置该用户其他会话的 2FA 状态，强制重新验证
    from app.core import db

    db.execute("UPDATE sessions SET two_fa_ok = 0 WHERE user_id = ? AND id != ?",
               (u["id"], sess.id if sess is not None else ""))
    audit.record("2fa_enable", username=u["username"], ip=client_ip(request))
    return redirect(request, "/")


@router.post("/2fa/disable")
def twofa_disable(request: Request, password: str = Form(...)):
    u = users_get_current(request)
    if u is None:
        return redirect(request, "/login")
    if not verify_password(password, u["password_hash"]):
        resp = redirect(request, "/settings/account")
        toast(resp, "密码校验失败，未关闭两步验证", level="error")
        return resp
    users.set_totp(u["id"], None, enabled=False)
    audit.record("2fa_disable", username=u["username"], ip=client_ip(request))
    resp = redirect(request, "/settings/account")
    toast(resp, "两步验证已关闭")
    return resp
