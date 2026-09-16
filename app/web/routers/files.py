"""文件工作台路由（W3 体验版）：薄控制器，一切能力在 workspace_service。

守卫（S5）：读=require_login；写=require_admin；CSRF router 级。
安全（S1/S4）：越狱与非法路径 → 审计 denied + 4xx。
"""
from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response

from app.core import audit
from app.hermes import engineering_service as eng
from app.hermes import workspace_service as ws
from app.hermes.paths import detect
from app.web.deps import Admin, User, client_ip, csrf_guard
from app.web.htmx import redirect, toast
from app.web.templating import render, render_partial

router = APIRouter(prefix="/files", dependencies=[Depends(csrf_guard)])


def _fail(request: Request, exc: Exception, where: str):
    """统一异常映射：越狱→审计 denied+400；不存在→404；其余→400。"""
    if isinstance(exc, ws.UnsafePath):
        _deny(request, exc, where)
    if isinstance(exc, ws.NotFound):
        raise HTTPException(status_code=404, detail=str(exc))
    raise HTTPException(status_code=400, detail=str(exc))


def _deny(request: Request, exc: Exception, where: str):
    u = getattr(request.state, "user", None)
    audit.record("files_denied",
                 username=u["username"] if u is not None else None,
                 target=where, detail=str(exc), ip=client_ip(request), outcome="denied")
    raise HTTPException(status_code=400, detail=str(exc))


def _list_or_404(request: Request, rel: str):
    try:
        return ws.list_dir(rel)
    except ws.WorkspaceError as exc:
        _fail(request, exc, rel or "/")


# ---------------------------------------------------------------------------
# 页面与片段
# ---------------------------------------------------------------------------

@router.get("")
def files_page(request: Request, user: User, path: str = ""):
    status = ws.workspace_status()
    ctx = {
        "nav_active": "workbench",
        "ws_status": status,
        "path": path,
        "crumbs": [],
        "entries": [],
        "inspect": None,
        "is_admin": user["role"] == "admin",
    }
    if status.get("error") or not status.get("exists"):
        return render(request, "files.html", ctx)
    ctx["crumbs"] = ws.breadcrumbs(path)
    ctx["entries"] = _list_or_404(request, path)
    ctx["inspect"] = ws.inspect_summary()
    audit.record("files_browse", username=user["username"], target=path or "/",
                 ip=client_ip(request))
    return render(request, "files.html", ctx)


@router.get("/list")
def list_fragment(request: Request, user: User, path: str = ""):
    """HTMX 片段：切换目录只换中栏。"""
    entries = _list_or_404(request, path)
    return render_partial(request, "files/_list.html", {
        "entries": entries, "path": path, "crumbs": ws.breadcrumbs(path),
        "is_admin": user["role"] == "admin",
    })


@router.get("/preview")
def preview_fragment(request: Request, user: User, path: str = ""):
    try:
        data = ws.read_preview(path)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    return render_partial(request, "files/_preview.html", {"pv": data})


# ---------------------------------------------------------------------------
# 原始字节 / 打包
# ---------------------------------------------------------------------------

@router.get("/raw")
def raw(request: Request, user: User, path: str = "", dl: int = 0):
    try:
        p = ws.file_for_download(path)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    media = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    audit.record("files_download" if dl else "files_view_file",
                 username=user["username"], target=path, ip=client_ip(request),
                 outcome="ok")
    return FileResponse(p, media_type=media, filename=p.name if dl else None,
                        content_disposition_type="attachment" if dl else "inline")


@router.get("/zip")
def zip_dir(request: Request, user: User, path: str = ""):
    try:
        blob, name = ws.zip_rel(path)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    audit.record("files_zip", username=user["username"], target=path, ip=client_ip(request))
    return Response(blob, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------------------
# 写操作（admin）
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload(request: Request, admin: Admin, file: UploadFile, path: str = Form("")):
    try:
        rel = ws.upload(file.filename or "", await file.read())
    except ws.WorkspaceError as exc:
        _fail(request, exc, "upload")
    audit.record("files_upload", username=admin["username"], target=rel,
                 ip=client_ip(request))
    resp = redirect(request, f"/files?path={rel.rsplit('/', 1)[0]}")
    toast(resp, f"已上传到 {rel}")
    return resp


@router.post("/move")
def move(request: Request, admin: Admin, path: str = Form(...)):
    try:
        rel = ws.move_to_archive(path)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    audit.record("files_archive", username=admin["username"], target=f"{path} → {rel}",
                 ip=client_ip(request))
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    entries = _list_or_404(request, parent)
    resp = render_partial(request, "files/_list.html", {
        "entries": entries, "path": parent, "crumbs": ws.breadcrumbs(parent),
        "is_admin": True,
    })
    toast(resp, f"已归档：{rel}")
    return resp


@router.post("/init")
def init_workspace(request: Request, admin: Admin):
    s = eng.load_settings()
    created = eng.init_workspace(s, detect())
    audit.record("files_workspace_init", username=admin["username"],
                 detail=created, ip=client_ip(request))
    resp = redirect(request, "/files")
    toast(resp, f"工作区已初始化（{len(created)} 项）" if created else "工作区原本已就绪")
    return resp
