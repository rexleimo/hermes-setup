"""MCP 服务路由：已配置列表、CRUD、启停、官方热门目录一键添加。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from app.core import audit
from app.hermes import mcp_service as svc
from app.hermes.mcp_service import McpError, McpServerInput
from app.web.deps import Admin, User, client_ip, csrf_guard
from app.web.htmx import is_htmx, redirect, toast
from app.web.templating import render

router = APIRouter(prefix="/mcp", dependencies=[Depends(csrf_guard)])


@router.get("")
def list_page(request: Request, user: User):
    servers = svc.list_servers()
    return render(request, "mcp/list.html", {
        "nav_active": "mcp",
        "servers": servers,
        "catalog": svc.catalog(),
        "catalog_available": svc.catalog_dir().is_dir(),
        "installed": {s.name for s in servers},
    })


# ---------------------------------------------------------------------------
# 新建 / 编辑
# ---------------------------------------------------------------------------

def _input_from_form(**kw) -> McpServerInput:
    return McpServerInput(
        transport=kw.get("transport", "stdio"),
        use_sse=kw.get("use_sse", "0") == "1",
        command=kw.get("command", ""),
        args_text=kw.get("args_text", ""),
        env_text=kw.get("env_text", ""),
        url=kw.get("url", ""),
        headers_text=kw.get("headers_text", ""),
        enabled=kw.get("enabled", "0") == "1",
        timeout=kw.get("timeout", ""),
        trust=kw.get("trust", "full"),
        auth_oauth=kw.get("auth_oauth", "0") == "1",
        secret_env_text=kw.get("secret_env_text", ""),
        secret_headers_text=kw.get("secret_headers_text", ""),
    )


@router.get("/new")
def new_page(request: Request, user: Admin, transport: str = "stdio", catalog: str = ""):
    entry = None
    if catalog:
        try:
            entry = svc.catalog_entry(catalog)
        except McpError:
            return _reject(request, f"官方目录中不存在：{catalog}", code=404)
        transport = "stdio" if entry.transport == "stdio" else "http"
    return render(request, "mcp/new.html", {
        "nav_active": "mcp",
        "entry": entry,
        "transport": transport if transport in svc.TRANSPORTS else "stdio",
        "error": "",
        "form": {},
    })


@router.post("/new")
def create(request: Request, user: Admin,
           name: str = Form(""), transport: str = Form("stdio"),
           use_sse: str = Form("0"),
           command: str = Form(""), args_text: str = Form(""),
           env_text: str = Form(""), url: str = Form(""),
           headers_text: str = Form(""), enabled: str = Form("0"),
           timeout: str = Form(""), trust: str = Form("full"),
           auth_oauth: str = Form("0"), secret_env_text: str = Form(""),
           secret_headers_text: str = Form("")):
    form = {"name": name, "command": command, "args_text": args_text,
            "env_text": env_text, "url": url, "headers_text": headers_text,
            "timeout": timeout, "trust": trust}
    try:
        notes = svc.create_server(name.strip(), _input_from_form(**locals()))
    except McpError as exc:
        return render(request, "mcp/new.html", {
            "nav_active": "mcp", "entry": None, "transport": transport,
            "error": str(exc), "form": form,
        }, status_code=400)
    audit.record("mcp_create", username=user["username"], target=name.strip(),
                 detail={"transport": transport}, ip=client_ip(request))
    resp = redirect(request, f"/mcp/{name.strip()}")
    toast(resp, f"MCP 服务 {name.strip()} 已添加；" + "；".join(notes))
    return resp


@router.get("/{name}")
def detail(request: Request, user: User, name: str):
    try:
        server = svc.get_server(name)
    except McpError:
        return _reject(request, f"MCP 服务不存在：{name}", code=404)
    return render(request, "mcp/detail.html", {
        "nav_active": "mcp",
        "s": server,
    })


@router.post("/{name}/update")
def update(request: Request, user: Admin, name: str,
           transport: str = Form("stdio"), use_sse: str = Form("0"),
           command: str = Form(""), args_text: str = Form(""),
           env_text: str = Form(""), url: str = Form(""),
           headers_text: str = Form(""), enabled: str = Form("0"),
           timeout: str = Form(""), trust: str = Form("full"),
           auth_oauth: str = Form("0"), secret_env_text: str = Form(""),
           secret_headers_text: str = Form("")):
    try:
        notes = svc.update_server(name, _input_from_form(**locals()))
    except McpError as exc:
        return _reject(request, str(exc))
    audit.record("mcp_update", username=user["username"], target=name,
                 ip=client_ip(request))
    resp = redirect(request, f"/mcp/{name}")
    toast(resp, "MCP 服务已更新；" + "；".join(notes))
    return resp


@router.post("/{name}/toggle")
def toggle(request: Request, user: Admin, name: str, enable: str = Form(...)):
    try:
        svc.set_enabled(name, enable == "1")
    except McpError as exc:
        return _reject(request, str(exc))
    audit.record("mcp_toggle", username=user["username"], target=name,
                 detail={"enabled": enable == "1"}, ip=client_ip(request))
    resp = redirect(request, "/mcp")
    toast(resp, f"MCP 服务 {name} 已{'启用' if enable == '1' else '停用'}")
    return resp


@router.post("/{name}/delete")
def delete(request: Request, user: Admin, name: str, confirm: str = Form("")):
    if confirm.strip() != name:
        audit.record("mcp_delete", username=user["username"], target=name,
                     outcome="denied", detail="确认词不匹配", ip=client_ip(request))
        return _reject(request, "请输入服务名称以确认删除")
    try:
        notes = svc.delete_server(name)
    except McpError as exc:
        return _reject(request, str(exc))
    audit.record("mcp_delete", username=user["username"], target=name,
                 detail=notes, ip=client_ip(request))
    resp = redirect(request, "/mcp")
    toast(resp, f"MCP 服务 {name} 已删除；" + "；".join(notes), level="warning")
    return resp


# ---------------------------------------------------------------------------
# 官方热门目录
# ---------------------------------------------------------------------------

@router.post("/catalog/{cid}/add")
def add_from_catalog(request: Request, user: Admin, cid: str):
    try:
        notes = svc.install_from_catalog(cid)
    except McpError as exc:
        return _reject(request, str(exc))
    audit.record("mcp_catalog_install", username=user["username"], target=cid,
                 ip=client_ip(request))
    resp = redirect(request, f"/mcp/{cid}")
    toast(resp, f"{cid} 已添加；" + "；".join(notes))
    return resp


# ---------------------------------------------------------------------------

def _reject(request: Request, message: str, code: int = 400):
    if is_htmx(request):
        from fastapi.responses import HTMLResponse

        return HTMLResponse(f"<div class='alert alert-danger'>{message}</div>", status_code=code)
    return render(request, "error.html",
                  {"nav_active": "mcp", "code": code, "message": message},
                  status_code=code)
