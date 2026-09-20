"""文件工作台路由（W3 体验版）：薄控制器，一切能力在 workspace_service。

守卫（S5）：读=require_login；写=require_admin；CSRF router 级。
安全（S1/S4）：越狱与非法路径 → 审计 denied + 4xx。
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

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
def files_page(request: Request, user: User, path: str = "", cat: str = "",
               sort: str = "name", view: str = "grid"):
    status = ws.workspace_status()
    ctx = {
        "nav_active": "workbench",
        "ws_status": status,
        "path": path, "cat": cat, "sort": sort, "view": view,
        "crumbs": [], "entries": [], "inspect": None,
        "counts": [], "locs": [],
        "is_admin": user["role"] == "admin",
        "ws_root_name": Path(status.get("root", "") or "hermes-workspace").name,
    }
    if status.get("error") or not status.get("exists"):
        return render(request, "files.html", ctx)
    try:
        ctx["entries"], ctx["crumbs"] = _listing(request, path, cat, sort)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path or cat or "/")
        return render(request, "files.html", ctx)
    ctx["counts"] = ws.category_counts()
    ctx["locs"] = ws.location_counts()
    ctx["inspect"] = ws.inspect_summary()
    audit.record("files_browse", username=user["username"],
                 target=f"{path or cat or '/'}", ip=client_ip(request))
    return render(request, "files.html", ctx)


def _listing(request: Request, path: str, cat: str, sort: str):
    """三种视图模式：目录 / 智能集合 / 最近。返回 (entries, crumbs)。"""
    if cat == "recent":
        return ws.list_recent(), [("最近使用", "?cat=recent")]
    if cat:
        if cat not in ws.CATEGORY_MAP:
            raise ws.WorkspaceError(f"未知集合：{cat}")
        label = ws.CATEGORY_MAP[cat][0]
        return ws.list_collection(cat), [(f"{label}（智能集合）", f"?cat={cat}")]
    return ws.list_dir(path, sort), ws.breadcrumbs(path)


@router.get("/list")
def list_fragment(request: Request, user: User, path: str = "", cat: str = "",
                  sort: str = "name", view: str = "grid"):
    """HTMX 片段（兼容保留）；页面导航主走 hx-boost 整页。"""
    try:
        entries, crumbs = _listing(request, path, cat, sort)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path or cat or "/")
        return
    return render_partial(request, "files/_content.html", {
        "entries": entries, "path": path, "cat": cat, "sort": sort,
        "view": view, "crumbs": crumbs,
        "is_admin": user["role"] == "admin",
        "ws_root_name": Path(ws.root()).name,
    })


@router.get("/preview")
def preview_fragment(request: Request, user: User, path: str = ""):
    try:
        data = ws.read_preview(path)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    return render_partial(request, "files/_preview.html", {"pv": data})


@router.get("/viewer")
def viewer_descriptor(request: Request, user: User, path: str = ""):
    """双击窗口层的内容描述符（JSON）：前端按 kind 查 Viewer 注册表构建视图。

    新增文件类型 = preview_class 加一个分支 + 前端注册表加一个条目；
    本端点保持稳定。src 由前端按 rel 拼（/files/raw），不在此重复。"""
    from fastapi.responses import JSONResponse

    try:
        p = ws.file_for_download(path)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    kind = ws.preview_class(path)
    size = p.stat().st_size
    desc: dict = {
        "ok": kind != "none",
        "kind": kind,
        "rel": path,
        "name": p.name,
        "size": size,
    }
    if kind == "text":
        data = p.read_bytes()[:ws.PREVIEW_LIMIT]
        desc["text"] = data.decode("utf-8", errors="replace")
        desc["truncated"] = size > ws.PREVIEW_LIMIT
    audit.record("files_open", username=user["username"],
                 target=f"{path}（{kind}）", ip=client_ip(request),
                 outcome="ok" if kind != "none" else "unsupported")
    return JSONResponse(desc)


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
    resp = render_partial(request, "files/_content.html", {
        "entries": entries, "path": parent, "cat": "", "sort": "name", "view": "grid",
        "crumbs": ws.breadcrumbs(parent), "is_admin": True,
        "ws_root_name": Path(ws.root()).name,
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
