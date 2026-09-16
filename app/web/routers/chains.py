"""链路配置：主模型概览、备选链（fallback providers）、模型别名管理。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from app.core import audit
from app.hermes.channels_service import list_channels
from app.hermes.paths import detect
from app.hermes.providers_service import (
    ChainEntry, ProviderError, get_fallback, list_aliases, list_providers,
    remove_alias, set_fallback,
)
from app.hermes import supervisor
from app.web.deps import User, client_ip, csrf_guard
from app.web.htmx import redirect, toast
from app.web.templating import render

router = APIRouter(prefix="/chains", dependencies=[Depends(csrf_guard)])


@router.get("")
def chains_page(request: Request, user: User):
    providers = list_providers()
    main = next((p for p in providers if p.is_main), None)
    fallback = get_fallback()
    aliases = list_aliases()
    return render(request, "chains.html", {
        "nav_active": "chains",
        "providers": providers,
        "main": main,
        "fallback": fallback,
        "aliases": aliases,
    })


@router.post("/fallback/add")
def fallback_add(request: Request, user: User, provider: str = Form(...),
                 model: str = Form(...), base_url: str = Form(""),
                 key_env: str = Form("")):
    entries = get_fallback()
    entries.append(ChainEntry(provider=provider.strip(), model=model.strip(),
                              base_url=base_url.strip(), key_env=key_env.strip()))
    try:
        set_fallback(entries)
    except Exception as exc:
        return _reject(request, f"写入失败：{exc}")
    audit.record("chain_fallback_add", username=user["username"],
                 detail={"provider": provider, "model": model}, ip=client_ip(request))
    resp = redirect(request, "/chains")
    toast(resp, "备选链已更新（写入 delegation.fallback_providers）")
    return resp


@router.post("/fallback/move")
def fallback_move(request: Request, user: User, position: int = Form(...),
                  direction: str = Form(...)):
    entries = get_fallback()
    idx = position - 1
    swap = idx + 1 if direction == "down" else idx - 1
    if not (0 <= idx < len(entries)) or not (0 <= swap < len(entries)):
        return _reject(request, "位置越界")
    entries[idx], entries[swap] = entries[swap], entries[idx]
    set_fallback(entries)
    audit.record("chain_fallback_move", username=user["username"],
                 detail=f"#{position} {direction}", ip=client_ip(request))
    resp = redirect(request, "/chains")
    toast(resp, "备选链顺序已调整")
    return resp


@router.post("/fallback/remove")
def fallback_remove(request: Request, user: User, position: int = Form(...)):
    entries = get_fallback()
    if not (1 <= position <= len(entries)):
        return _reject(request, "位置越界")
    removed = entries.pop(position - 1)
    set_fallback(entries)
    audit.record("chain_fallback_remove", username=user["username"],
                 detail=f"{removed.provider}/{removed.model}", ip=client_ip(request))
    resp = redirect(request, "/chains")
    toast(resp, f"已移除备选项 {removed.provider}/{removed.model}")
    return resp


@router.post("/alias/remove")
def alias_remove(request: Request, user: User, alias: str = Form(...)):
    remove_alias(alias)
    audit.record("alias_remove", username=user["username"], target=alias,
                 ip=client_ip(request))
    resp = redirect(request, "/chains")
    toast(resp, f"别名 {alias} 已移除")
    return resp


def _reject(request: Request, message: str, code: int = 400):
    from fastapi.responses import HTMLResponse

    from app.web.htmx import is_htmx

    if is_htmx(request):
        return HTMLResponse(f"<div class='alert alert-danger'>{message}</div>", status_code=code)
    return render(request, "error.html",
                  {"nav_active": "chains", "code": code, "message": message},
                  status_code=code)
