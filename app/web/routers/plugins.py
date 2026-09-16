"""插件与 Hook 路由：插件白名单启停、官方策展目录安装、shell hooks CRUD、
信任白名单撤销、gateway hooks / outbound webhooks 只读盘点。

注意路由顺序：固定前缀（/catalog、/hooks、/settings）必须注册在
/{name}/* 参数路由之前，否则 /plugins/hooks/delete 会被 /{name}/delete 吃掉。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from app.core import audit
from app.hermes import hooks_service as hsvc
from app.hermes import plugins_service as psvc
from app.hermes.hooks_service import HookError
from app.hermes.plugins_service import PluginError
from app.web.deps import Admin, User, client_ip, csrf_guard
from app.web.htmx import is_htmx, redirect, toast
from app.web.templating import render

router = APIRouter(prefix="/plugins", dependencies=[Depends(csrf_guard)])


@router.get("")
def list_page(request: Request, user: User):
    plugins = psvc.list_plugins()
    return render(request, "plugins/list.html", {
        "nav_active": "plugins",
        "plugins": plugins,
        "catalog": psvc.catalog(),
        "installed_names": {p.name for p in plugins},
        "hook_timeout": psvc.get_hook_timeout(),
        "hooks": hsvc.list_hooks(),
        "auto_accept": hsvc.get_auto_accept(),
        "allowlist": hsvc.allowlist(),
        "gateway_hooks": hsvc.gateway_hooks(),
        "outbound": hsvc.outbound_webhooks(),
        "valid_hooks": sorted(hsvc.VALID_HOOKS),
    })


# ---------------------------------------------------------------------------
# Shell hooks CRUD（固定前缀，须先于 /{name}/* 注册）
# ---------------------------------------------------------------------------

@router.post("/hooks/create")
def hook_create(request: Request, user: Admin,
                event: str = Form(...), command: str = Form(...),
                matcher: str = Form(""), timeout: str = Form(""),
                fail_closed: str = Form("0")):
    try:
        notes = hsvc.create_hook(event=event, command=command, matcher=matcher,
                                 timeout=timeout, fail_closed=fail_closed == "1")
    except HookError as exc:
        return _reject(request, str(exc))
    audit.record("hook_create", username=user["username"], target=event,
                 detail={"command": command[:200]}, ip=client_ip(request))
    resp = redirect(request, "/plugins")
    toast(resp, "Shell hook 已保存；" + "；".join(notes)
          + "（首次触发需确认，或打开自动接受）")
    return resp


@router.post("/hooks/delete")
def hook_delete(request: Request, user: Admin,
                event: str = Form(...), command: str = Form(...)):
    try:
        hsvc.delete_hook(event.strip(), command.strip())
    except HookError as exc:
        return _reject(request, str(exc))
    audit.record("hook_delete", username=user["username"], target=event,
                 detail={"command": command[:200]}, ip=client_ip(request))
    resp = redirect(request, "/plugins")
    toast(resp, f"已删除 {event} 的 hook", level="warning")
    return resp


@router.post("/hooks/auto-accept")
def hook_auto_accept(request: Request, user: Admin, enable: str = Form(...)):
    hsvc.set_auto_accept(enable == "1")
    audit.record("hook_auto_accept", username=user["username"],
                 detail={"enabled": enable == "1"}, ip=client_ip(request))
    resp = redirect(request, "/plugins")
    if enable == "1":
        toast(resp, "已开启自动接受 —— shell hook 首次触发不再询问（安全性下降，请谨慎）",
              level="warning")
    else:
        toast(resp, "已关闭自动接受，恢复首次触发确认")
    return resp


@router.post("/hooks/revoke")
def hook_revoke(request: Request, user: Admin, command: str = Form(...)):
    try:
        removed = hsvc.revoke_allowlist_entry(command.strip())
    except HookError as exc:
        return _reject(request, str(exc))
    audit.record("hook_revoke", username=user["username"],
                 detail={"command": command.strip()[:200], "removed": removed},
                 ip=client_ip(request))
    resp = redirect(request, "/plugins")
    toast(resp, f"已撤销信任（{removed} 条）；运行中的会话需重启生效", level="warning")
    return resp


# ---------------------------------------------------------------------------
# 官方策展目录 / 插件超时设置（同为固定前缀）
# ---------------------------------------------------------------------------

@router.post("/catalog/install")
def install(request: Request, user: Admin, name: str = Form(...)):
    try:
        job_id = psvc.install(name.strip())
    except PluginError as exc:
        return _reject(request, str(exc))
    audit.record("plugin_install", username=user["username"], target=name.strip(),
                 detail=f"后台任务 #{job_id}（hermes plugins install）",
                 ip=client_ip(request))
    resp = redirect(request, "/plugins")
    toast(resp, f"插件 {name.strip()} 安装任务已提交（后台任务 #{job_id}），"
                f"进度见「服务管理 → 后台任务」；完成后回本页开启白名单")
    return resp


@router.post("/settings/timeout")
def save_timeout(request: Request, user: Admin, timeout: str = Form(...)):
    if not timeout.strip().isdigit():
        return _reject(request, "超时秒数必须是正整数")
    try:
        psvc.set_hook_timeout(int(timeout.strip()))
    except PluginError as exc:
        return _reject(request, str(exc))
    audit.record("plugin_timeout", username=user["username"],
                 detail={"hook_callback_timeout": int(timeout.strip())},
                 ip=client_ip(request))
    resp = redirect(request, "/plugins")
    toast(resp, "插件 hook 回调超时已更新")
    return resp


# ---------------------------------------------------------------------------
# 单个插件操作（/{name}/* 参数路由，放在最后）
# ---------------------------------------------------------------------------

@router.post("/{name}/toggle")
def toggle(request: Request, user: Admin, name: str, enable: str = Form(...)):
    try:
        psvc.set_enabled(name, enable == "1")
    except PluginError as exc:
        return _reject(request, str(exc))
    audit.record("plugin_toggle", username=user["username"], target=name,
                 detail={"enabled": enable == "1"}, ip=client_ip(request))
    resp = redirect(request, "/plugins")
    toast(resp, f"插件 {name} 已{'启用（重启会话后加载）' if enable == '1' else '禁用'}")
    return resp


@router.post("/{name}/delete")
def delete(request: Request, user: Admin, name: str, confirm: str = Form("")):
    if confirm.strip() != name:
        audit.record("plugin_delete", username=user["username"], target=name,
                     outcome="denied", detail="确认词不匹配", ip=client_ip(request))
        return _reject(request, "请输入插件名以确认删除")
    try:
        notes = psvc.remove_plugin(name)
    except PluginError as exc:
        return _reject(request, str(exc))
    audit.record("plugin_delete", username=user["username"], target=name,
                 detail=notes, ip=client_ip(request))
    resp = redirect(request, "/plugins")
    toast(resp, f"插件 {name} 已删除；" + "；".join(notes), level="warning")
    return resp


# ---------------------------------------------------------------------------

def _reject(request: Request, message: str, code: int = 400):
    if is_htmx(request):
        from fastapi.responses import HTMLResponse

        return HTMLResponse(f"<div class='alert alert-danger'>{message}</div>", status_code=code)
    return render(request, "error.html",
                  {"nav_active": "plugins", "code": code, "message": message},
                  status_code=code)
