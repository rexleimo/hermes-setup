"""文件工作台路由（W3 体验版）：薄控制器，一切能力在 workspace_service。

守卫（S5）：读=require_login；写=require_admin；CSRF router 级。
安全（S1/S4）：越狱与非法路径 → 审计 denied + 4xx。
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from app.core import audit
from app.hermes import engineering_service as eng
from app.hermes import workspace_service as ws
from app.hermes.paths import detect
from app.web.deps import Admin, User, client_ip, csrf_guard
from app.web.htmx import is_htmx, redirect, toast
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


def _side_oob(request: Request, cat: str, path: str, sort: str, view: str) -> bytes:
    """侧栏 OOB 片段：归档/上传后计数变化，随响应换入 #osfm-side。"""
    side = render_partial(request, "files/_side.html", {
        "cat": cat, "path": path, "sort": sort, "view": view,
        "locs": ws.location_counts(), "counts": ws.category_counts(),
    }).body
    return (b'<aside class="e-side" id="osfm-side" hx-swap-oob="true">'
            + side + b"</aside>")


# ---------------------------------------------------------------------------
# 页面与片段
# ---------------------------------------------------------------------------

@router.get("")
def files_page(request: Request, user: User, path: str = "", cat: str = "",
               sort: str = "name", view: str = "grid", q: str = ""):
    status = ws.workspace_status()
    ctx = {
        "nav_active": "workbench",
        "ws_status": status,
        "path": path, "cat": cat, "sort": sort, "view": view, "q": q,
        "crumbs": [], "entries": [], "inspect": None,
        "counts": [], "locs": [],
        "is_admin": user["role"] == "admin",
        "ws_root_name": Path(status.get("root", "") or "hermes-workspace").name,
    }
    if status.get("error") or not status.get("exists"):
        return render(request, "files.html", ctx)
    try:
        if q.strip():
            # 全局搜索（W15）：按名字子串匹配整个工作区，独立于 path/cat
            ctx["entries"] = ws.search(q)
            ctx["crumbs"] = [(f"搜索「{q.strip()}」", f"?q={q.strip()}")]
        else:
            ctx["entries"], ctx["crumbs"] = _listing(request, path, cat, sort)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path or cat or "/")
        return render(request, "files.html", ctx)
    ctx["counts"] = ws.category_counts()
    ctx["locs"] = ws.location_counts()
    ctx["inspect"] = ws.inspect_summary()
    audit.record("files_browse", username=user["username"],
                 target=f"{path or cat or '/'}" + (f"（搜索 {q.strip()}）" if q.strip() else ""),
                 ip=client_ip(request))
    return render(request, "files.html", ctx)


def _listing(request: Request, path: str, cat: str, sort: str):
    """四种视图模式：目录 / 智能集合 / 最近 / 回收站。返回 (entries, crumbs)。"""
    if cat == "recent":
        return ws.list_recent(), [("最近使用", "?cat=recent")]
    if cat == "trash":
        return ws.trash_list(), [("回收站", "?cat=trash")]
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
    # 审计只记「下载」：浏览/缩略图每个 <img> 都写一条 INSERT，打开一个图片
    # 目录就是几十上百条审计 + 写放大（0.8.30 前的行为，审计表因此注水）。
    if dl:
        audit.record("files_download", username=user["username"], target=path,
                     ip=client_ip(request), outcome="ok")
    # 工作区文件会随时变化（Agent 产出）：no-cache 强制浏览器按 ETag 再验证，
    # 避免 viewer/缩略图拿到更新前的旧内容（E2E 实锤过坏图缓存问题）
    return FileResponse(p, media_type=media, filename=p.name if dl else None,
                        content_disposition_type="attachment" if dl else "inline",
                        headers={"Cache-Control": "no-cache"})


@router.get("/raw-html")
def raw_html(request: Request, user: User, path: str = ""):
    """HTML 宽松档预览源（WI-19B）：仅 .html/.htm，其余类型 400。

    字节与 /files/raw 同源；响应头由中间件换成短名单 CDN 的宽松 CSP
    （preview_csp），并叠加 sandbox 指令——“新标签页打开”的顶层文档
    同样被沙盒，无身份，带不走工作区数据。
    """
    if Path(path).suffix.lower() not in (".html", ".htm"):
        raise HTTPException(status_code=400, detail="宽松预览仅支持 HTML 文件")
    try:
        p = ws.file_for_download(path)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    media = mimetypes.guess_type(p.name)[0] or "text/html"
    audit.record("files_view_file", username=user["username"], target=path,
                 ip=client_ip(request), outcome="ok")
    return FileResponse(p, media_type=media, content_disposition_type="inline",
                        headers={"Cache-Control": "no-cache"})


@router.get("/zip")
def zip_dir(request: Request, user: User, path: str = ""):
    try:
        blob, name = ws.zip_rel(path)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    audit.record("files_zip", username=user["username"], target=path, ip=client_ip(request))
    return Response(blob, media_type="application/zip",
                    headers={"Cache-Control": "no-cache",
                             "Content-Disposition": f'attachment; filename="{name}"'})


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
    dest = rel.rsplit("/", 1)[0] if "/" in rel else ""
    nav = f"/files?path={dest}" if dest else "/files"
    if is_htmx(request):
        # HTMX 1.9 的 HX-Redirect 是整页跳转（白屏）：改发 osfm:nav 事件，
        # 前端 app.js 走 boosted 导航（无白屏 + 历史记录）。
        resp = Response(status_code=200)
        toast(resp, f"已上传到 {rel}", extra={"osfm:nav": {"to": nav}})
        return resp
    resp = redirect(request, nav)
    toast(resp, f"已上传到 {rel}")
    return resp


@router.post("/move")
def move(request: Request, admin: Admin, path: str = Form(...), here: str = Form(""),
         dest: str = Form("")):
    """移动：dest 为空 = 归档语义（mv 进 archive/）；给了 dest = 移入指定目录
   （拖拽放入文件夹）。原地重渲染当前视图 + 侧栏 OOB，不整页刷新。"""
    try:
        if dest.strip():
            rel = ws.move_entry(path, dest)
        else:
            rel = ws.move_to_archive(path)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    audit.record("files_move", username=admin["username"], target=f"{path} → {rel}",
                 ip=client_ip(request))
    return _rerender(request, here, f"已移动：{rel}" if dest.strip() else f"已归档：{rel}")


@router.post("/rename")
def rename(request: Request, admin: Admin, path: str = Form(...), name: str = Form(...),
           here: str = Form("")):
    try:
        rel = ws.rename_entry(path, name)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    audit.record("files_rename", username=admin["username"], target=f"{path} → {rel}",
                 ip=client_ip(request))
    return _rerender(request, here, f"已重命名：{rel}")


@router.post("/delete")
def delete(request: Request, admin: Admin, path: str = Form(...), here: str = Form("")):
    """软删除：移入回收站 .trash（可还原），不直接落盘抹除。"""
    try:
        rel = ws.delete_entry(path)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    audit.record("files_delete", username=admin["username"], target=f"{path} → {rel}",
                 ip=client_ip(request))
    return _rerender(request, here, f"已移入回收站：{path}")


@router.post("/restore")
def restore(request: Request, admin: Admin, path: str = Form(...), here: str = Form("")):
    try:
        rel = ws.restore_entry(path)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    audit.record("files_restore", username=admin["username"], target=f"{path} → {rel}",
                 ip=client_ip(request))
    return _rerender(request, here, f"已还原：{rel}")


@router.post("/copy")
def copy(request: Request, admin: Admin, path: str = Form(...), here: str = Form("")):
    try:
        rel = ws.copy_entry(path)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    audit.record("files_copy", username=admin["username"], target=f"{path} → {rel}",
                 ip=client_ip(request))
    return _rerender(request, here, f"已复制副本：{rel}")


@router.post("/new")
def new_entry(request: Request, admin: Admin, dir_: str = Form("", alias="dir"),
              kind: str = Form("folder"), name: str = Form(...), here: str = Form("")):
    if kind not in ("folder", "file"):
        return _fail(request, ws.WorkspaceError("未知类型"), "new")
    try:
        rel = ws.mkdir_entry(dir_, name) if kind == "folder" else ws.mkfile_entry(dir_, name)
    except ws.WorkspaceError as exc:
        _fail(request, exc, dir_)
    audit.record(f"files_new_{kind}", username=admin["username"], target=rel,
                 ip=client_ip(request))
    return _rerender(request, here, f"已创建：{rel}")


@router.post("/batch")
async def batch(request: Request, admin: Admin, here: str = Form("")):
    """批量操作：op ∈ delete/copy/archive，paths 为多值表单字段。"""
    form = await request.form()
    op = str(form.get("op") or "")
    rels = [str(v) for v in form.getlist("paths") if str(v).strip()]
    if op not in ("delete", "copy", "archive") or not rels:
        _fail(request, ws.WorkspaceError("批量操作参数不合法"), "batch")
        return
    ops = {"delete": ws.delete_entry, "copy": ws.copy_entry, "archive": ws.move_to_archive}
    done, failed = [], []
    for r in rels:
        try:
            done.append(ops[op](r))
        except ws.WorkspaceError as exc:
            failed.append(f"{r}：{exc}")
    audit.record(f"files_batch_{op}", username=admin["username"],
                 detail={"count": len(done), "failed": failed}, ip=client_ip(request))
    label = {"delete": "移入回收站", "copy": "复制副本", "archive": "归档"}[op]
    msg = f"批量{label} {len(done)} 项"
    if failed:
        msg += f"，失败 {len(failed)} 项：{'；'.join(failed[:3])}"
    return _rerender(request, here, msg)


def _rerender(request: Request, here: str, message: str):
    """写操作后的统一收尾：原地重渲染当前视图（条目即时消失/出现）+ 侧栏 OOB。"""
    q = dict(parse_qsl(here, keep_blank_values=True)) if here else {}
    v_path, v_cat, v_q = q.get("path", ""), q.get("cat", ""), q.get("q", "")
    v_sort = q.get("sort", "name")
    v_view = q.get("view", "grid")
    if v_sort not in ("name", "time", "size"):
        v_sort = "name"
    if v_view not in ("grid", "list"):
        v_view = "grid"
    try:
        if v_q.strip():
            entries, crumbs = ws.search(v_q), [(f"搜索「{v_q.strip()}」", f"?q={v_q.strip()}")]
        else:
            entries, crumbs = _listing(request, v_path, v_cat, v_sort)
    except ws.WorkspaceError:
        parent = v_path.rsplit("/", 1)[0] if "/" in v_path else ""
        entries, crumbs = _list_or_404(request, parent), ws.breadcrumbs(parent)
    body = render_partial(request, "files/_content.html", {
        "entries": entries, "path": v_path, "cat": v_cat, "sort": v_sort, "view": v_view,
        "crumbs": crumbs, "is_admin": True,
        "ws_root_name": Path(ws.root()).name,
    }).body
    resp = Response(body + _side_oob(request, v_cat, v_path, v_sort, v_view),
                    media_type="text/html; charset=utf-8")
    toast(resp, message)
    return resp


@router.post("/save")
def save_file(request: Request, admin: Admin, path: str = Form(...),
              content: str = Form(""), mode: str = Form("overwrite"),
              name: str = Form("")):
    """窗口文本编辑保存：overwrite 覆盖原文件 / copy 同目录另存。

    JSON 响应（窗口内 fetch 调用，成功后前端弹 toast 并更新视图）。
    """
    if mode not in ("overwrite", "copy"):
        mode = "overwrite"
    try:
        rel = ws.save_text(path, content, mode, name)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    audit.record("files_save_" + mode, username=admin["username"],
                 target=f"{path}" + (f" → {rel}" if rel != path else ""),
                 ip=client_ip(request))
    return JSONResponse({"ok": True, "rel": rel, "mode": mode})


@router.post("/init")
def init_workspace(request: Request, admin: Admin):
    s = eng.load_settings()
    created = eng.init_workspace(s, detect())
    audit.record("files_workspace_init", username=admin["username"],
                 detail=created, ip=client_ip(request))
    resp = redirect(request, "/files")
    toast(resp, f"工作区已初始化（{len(created)} 项）" if created else "工作区原本已就绪")
    return resp


# ---------------------------------------------------------------------------
# 目录变更监听（W16）：Agent 在另一头写文件时，打开中的目录自动刷新。
# 轻量实现：SSE 每 2.5s 对当前目录做一次单层 (name, mtime) 签名对比，
# 变化才发事件；智能集合/最近视图是全树聚合，不做监听（连接立即收尾）。
# ---------------------------------------------------------------------------

@router.get("/watch")
def watch(request: Request, user: User, path: str = "", cat: str = ""):
    import json
    import time as _time

    from fastapi.responses import StreamingResponse

    def _payload(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    if cat not in ("", "trash"):
        # 聚合视图无固定目录可盯：直接收尾，前端不会重连这个视图
        return StreamingResponse(iter([_payload("bye", {})]),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    def _signature():
        if cat == "trash":
            items = ws.trash_list()
        else:
            target = ws.resolve_rel(path)
            if not target.is_dir():
                return None
            base = ws.root()
            items = []
            for p in sorted(target.iterdir()):
                if p.name.startswith("."):
                    continue
                try:
                    items.append(ws._entry(p, base))
                except OSError:
                    continue
        return sorted((e.name, round(e.mtime, 1), e.is_dir) for e in items)

    def _gen():
        last = None
        first = True
        deadline = _time.monotonic() + 1800
        while _time.monotonic() < deadline:
            try:
                sig = _signature()
            except ws.WorkspaceError:
                sig = None
            if first:
                yield _payload("hello", {})
                first = False
            elif sig != last and last is not None:
                yield _payload("changed", {})
            last = sig
            _time.sleep(2.5)
        yield _payload("bye", {})

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
