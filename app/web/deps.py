"""请求级依赖：会话装载、认证守卫（异常式短路）、CSRF、客户端 IP。

FastAPI 新版本不再对「依赖返回 Response」短路路由，因此守卫通过抛出
LoginRequired / Forbidden 异常 + 全局 exception handler 完成跳转与 403。
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request

from app.core import db, sessions as session_store
from app.core.csrf import csrf_token_for

LOGIN_URL = "/login"
TWO_FA_URL = "/login/2fa"


class LoginRequired(Exception):
    """未认证：携带应跳转的 URL。"""

    def __init__(self, url: str = LOGIN_URL) -> None:
        self.url = url


class Forbidden(Exception):
    """已认证但权限不足。"""


class CSRFFailed(Exception):
    """CSRF 令牌缺失或无效。"""


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def get_session(request: Request) -> session_store.Session | None:
    sid = request.cookies.get(session_store.COOKIE_NAME, "")
    sess = session_store.get(sid)
    request.state.session = sess
    return sess


def get_session_required(request: Request) -> session_store.Session | None:
    sess = getattr(request.state, "session", None)
    return sess if sess is not None else get_session(request)


def current_user(request: Request):
    sess = get_session_required(request)
    if sess is None or not sess.is_authenticated:
        return None
    user = db.query_one("SELECT * FROM users WHERE id = ?", (sess.user_id,))
    if user is None or not user["active"]:
        return None
    request.state.user = user
    return user


def _login_target(request: Request) -> str:
    nxt = request.url.path
    if nxt in ("/", LOGIN_URL) or nxt.startswith(LOGIN_URL) or nxt.startswith("/setup"):
        return LOGIN_URL
    return f"{LOGIN_URL}?next={nxt}"


def require_login(request: Request):
    """页面守卫：未登录抛 LoginRequired；TOTP 未完成抛向挑战页。"""
    sess = get_session_required(request)
    user = current_user(request)
    if user is None:
        raise LoginRequired(_login_target(request))
    if user["totp_enabled"] and not (sess and sess.two_fa_ok):
        raise LoginRequired(TWO_FA_URL)
    return user


def require_admin(request: Request):
    user = require_login(request)
    if user["role"] != "admin":
        raise Forbidden()
    return user


User = Annotated[Any, Depends(require_login)]
Admin = Annotated[Any, Depends(require_admin)]
MaybeUser = Annotated[Any, Depends(current_user)]


def csrf_token(request: Request) -> str:
    sess = get_session_required(request)
    if sess is None:
        return ""
    return csrf_token_for(sess.id)


async def csrf_guard(request: Request):
    """路由级 CSRF 校验依赖（挂在各 router 的 dependencies 上）。

    通过 request.form() 读取令牌 —— 与 FastAPI 的表单解析共享同一份缓存，
    不会像中间件那样消费掉请求体。
    """
    from app.core.csrf import SAFE_METHODS, validate as csrf_validate

    if request.method in SAFE_METHODS:
        return
    token = request.headers.get("x-csrf-token")
    if not token:
        try:
            form = await request.form()
            token = str(form.get("_csrf") or "")
        except Exception:
            token = ""
    sid = request.cookies.get(session_store.COOKIE_NAME, "")
    if not csrf_validate(sid, token):
        from app.core import audit

        audit.record(
            "csrf_reject", target=f"{request.method} {request.url.path}",
            outcome="denied", ip=client_ip(request),
        )
        raise CSRFFailed()
