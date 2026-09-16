"""模板环境与公共上下文。"""
from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app import __version__
from app.core.csrf import csrf_token_for
from app.web.deps import get_session_required

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _asset_ver() -> str:
    """静态资源版本号：取各文件 mtime 的最大值，用于 URL 缓存穿透。"""
    import hashlib

    mtimes = []
    for p in Path(TEMPLATES_DIR).resolve().parent.rglob("*"):
        if p.suffix in (".css", ".js") and "vendor" not in p.parts:
            try:
                mtimes.append(p.stat().st_mtime)
            except OSError:
                pass
    raw = ",".join(str(int(m)) for m in sorted(mtimes))
    return hashlib.md5(raw.encode()).hexdigest()[:10]


ASSET_VER = _asset_ver()


def mask_secret(value: str | None, keep: int = 4) -> str:
    if not value:
        return "未设置"
    if len(value) <= keep * 2:
        return "•" * 8
    return f"{value[:keep]}••••••••{value[-keep:]}"


def shorten(value: str | None, n: int = 60) -> str:
    if not value:
        return ""
    return value if len(value) <= n else value[: n - 1] + "…"


templates.env.filters["mask"] = mask_secret
templates.env.filters["shorten"] = shorten
templates.env.globals["APP_VERSION"] = __version__
templates.env.globals["ASSET_VER"] = ASSET_VER


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    """渲染页面模板并注入公共上下文。"""
    from app.core.appsettings import all_settings as _all_settings
    from app.core.settings import settings as _settings

    sess = get_session_required(request)
    user = getattr(request.state, "user", None)
    ctx = {
        "request": request,
        "user": user,
        "csrf_token": csrf_token_for(sess.id) if sess else "",
        "nav_active": context.get("nav_active", "") if context else "",
        "app_name": "Hermes Console",
        "secure_cookies": _settings.secure_cookies,
        "path_settings": _all_settings(),
    }
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def render_partial(request: Request, name: str, context: dict | None = None,
                   status_code: int = 200):
    """渲染 HTMX 片段（不带布局）。"""
    ctx = {"request": request}
    sess = get_session_required(request)
    ctx["csrf_token"] = csrf_token_for(sess.id) if sess else ""
    if context:
        ctx.update(context)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)
