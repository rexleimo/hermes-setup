"""Hermes Console 应用入口。

中间件（请求自外向内）：安全响应头 → IP 白名单 → CSRF 校验 → 会话装载。
"""
from __future__ import annotations

import ipaddress
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core import db, sessions as session_store
from app.core.settings import settings
from app.web.templating import render

log = logging.getLogger("hermes_console")

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    session_store.purge_expired()
    log.info("Hermes Console %s 启动于 %s:%s", "0.1.0", settings.host, settings.port)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Hermes Console", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)

    # ------------------------------------------------------------------
    # 路由
    # ------------------------------------------------------------------
    from app.web.routers import (
        audit as audit_router, auth as auth_router, catalog as catalog_router,
        chains as chains_router, channels as channels_router,
        dashboard as dashboard_router, engineering as engineering_router,
        memory as memory_router, providers as providers_router,
        service as service_router, settings as settings_router,
        users as users_router,
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(auth_router.router)
    app.include_router(dashboard_router.router)
    app.include_router(service_router.router)
    app.include_router(providers_router.router)
    app.include_router(channels_router.router)
    app.include_router(chains_router.router)
    app.include_router(memory_router.router)
    app.include_router(engineering_router.router)
    app.include_router(audit_router.router)
    app.include_router(users_router.router)
    app.include_router(settings_router.router)
    app.include_router(catalog_router.router)

    # ------------------------------------------------------------------
    # 中间件（add 顺序与执行顺序相反：最后 add 的最外层）
    # ------------------------------------------------------------------

    class SessionMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            sid = request.cookies.get(session_store.COOKIE_NAME, "")
            sess = session_store.get(sid)
            created = False
            if sess is None:
                sess = session_store.create(
                    user_id=None,
                    ip=request.client.host if request.client else "",
                    user_agent=request.headers.get("user-agent", "")[:256],
                )
                created = True
            request.state.session = sess
            response: Response = await call_next(request)
            if created and response.status_code < 500:
                response.set_cookie(
                    session_store.COOKIE_NAME, sess.id,
                    max_age=settings.session_ttl_minutes * 60,
                    httponly=True, samesite="strict",
                    secure=settings.secure_cookies or request.url.scheme == "https",
                    path="/",
                )
            return response

    class IpAllowlistMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            raw = settings.allowed_ips
            if raw:
                client = request.client.host if request.client else ""
                networks = []
                for item in raw.split(","):
                    item = item.strip()
                    if not item:
                        continue
                    try:
                        networks.append(ipaddress.ip_network(item, strict=False))
                    except ValueError:
                        continue
                try:
                    ok = any(ipaddress.ip_address(client) in n for n in networks)
                except ValueError:
                    ok = False
                if not ok:
                    return HTMLResponse("Forbidden", status_code=403)
            return await call_next(request)

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            h = response.headers
            h.setdefault("X-Frame-Options", "DENY")
            h.setdefault("X-Content-Type-Options", "nosniff")
            h.setdefault("Referrer-Policy", "same-origin")
            h.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                "script-src 'self'; frame-ancestors 'none'; base-uri 'self'",
            )
            return response

    app.add_middleware(SessionMiddleware)
    app.add_middleware(IpAllowlistMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # ------------------------------------------------------------------
    # 认证 / CSRF 异常 → 跳转 / 403
    # ------------------------------------------------------------------
    from app.web.deps import CSRFFailed, Forbidden, LoginRequired
    from app.web.htmx import htmx_redirect, is_htmx

    @app.exception_handler(LoginRequired)
    async def login_required(request: Request, exc: LoginRequired):
        if is_htmx(request):
            return htmx_redirect(exc.url)
        return HTMLResponse(status_code=302, headers={"Location": exc.url})

    @app.exception_handler(Forbidden)
    async def forbidden(request: Request, exc: Forbidden):
        from app.web.templating import render as _render

        if is_htmx(request):
            return HTMLResponse(
                "<div class='alert alert-danger'>需要管理员权限。</div>", status_code=403
            )
        try:
            return _render(request, "error.html",
                           {"nav_active": "", "code": 403,
                            "message": "该操作需要管理员角色。"}, status_code=403)
        except Exception:
            return HTMLResponse("403 Forbidden", status_code=403)

    @app.exception_handler(CSRFFailed)
    async def csrf_failed(request: Request, exc: CSRFFailed):
        if is_htmx(request):
            return HTMLResponse(
                "<div class='alert alert-danger'>CSRF 校验失败，请刷新页面重试。</div>",
                status_code=403)
        try:
            from app.web.templating import render as _render

            return _render(request, "error.html",
                           {"nav_active": "", "code": 403,
                            "message": "CSRF 校验失败：令牌缺失或已过期，请刷新页面重试。"},
                           status_code=403)
        except Exception:
            return HTMLResponse("403 CSRF Failure", status_code=403)

    # ------------------------------------------------------------------
    # 错误页
    # ------------------------------------------------------------------

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        try:
            return render(request, "error.html",
                          {"nav_active": "", "code": 404,
                           "message": "页面不存在或已被移动"}, status_code=404)
        except Exception:
            return HTMLResponse("404 Not Found", status_code=404)

    @app.exception_handler(500)
    async def server_error(request: Request, exc):
        log.exception("服务器内部错误: %s", exc)
        try:
            return render(request, "error.html",
                          {"nav_active": "", "code": 500,
                           "message": "服务器内部错误，详情见控制台日志"}, status_code=500)
        except Exception:
            return HTMLResponse("500 Internal Server Error", status_code=500)

    return app


app = create_app()


def main() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
