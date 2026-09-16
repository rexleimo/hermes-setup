"""HTMX / 响应辅助：toast 通知、片段判定、HX-Redirect。"""
from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import RedirectResponse, Response

TOAST_TRIGGER = "console:toast"


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def toast(response: Response, message: str, level: str = "success") -> None:
    """在响应上注入 toast 事件（前端 app.js 监听后弹出）。

    HTTP 头仅允许 latin-1，中文必须以 \\uXXXX 转义（htmx 会按 JSON 解码）。
    """
    response.headers["HX-Trigger"] = json.dumps(
        {TOAST_TRIGGER: {"message": message, "level": level}}, ensure_ascii=True
    )


def htmx_redirect(url: str) -> Response:
    resp = Response(status_code=200)
    resp.headers["HX-Redirect"] = url
    return resp


def redirect(request: Request, url: str, status_code: int = 303):
    """HTMX 请求用 HX-Redirect 头完成跳转，普通请求 303。"""
    if is_htmx(request):
        return htmx_redirect(url)
    return RedirectResponse(url, status_code=status_code)
