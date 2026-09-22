"""文件工作台路由（W3 体验版）：薄控制器，一切能力在 workspace_service。

守卫（S5）：读=require_login；写=require_admin；CSRF router 级。
安全（S1/S4）：越狱与非法路径 → 审计 denied + 4xx。
"""
from __future__ import annotations

import asyncio
import mimetypes
import os
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote

import anyio
from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from app.core import audit
from app.core.settings import settings
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


def _qs(path: str = "", cat: str = "", sort: str = "name", view: str = "grid",
        offset: int = 0, q: str = "") -> str:
    """查询串统一从这里出（页面链接 / 加载更多 / 写操作后重渲染共用一份口径）。"""
    if q.strip():
        head = f"q={quote(q.strip())}"
    elif cat:
        head = f"cat={cat}"
    elif path:
        head = f"path={quote(path)}"
    else:
        head = ""
    parts = [p for p in (head, f"sort={sort}", f"view={view}",
                         f"offset={offset}" if offset else "") if p]
    return "&".join(parts)


# ---------------------------------------------------------------------------
# 页面与片段
# ---------------------------------------------------------------------------

def _view_ctx(request: Request, user, path: str, cat: str, sort: str, view: str,
              q: str = "", offset: int = 0):
    """列表视图的部分上下文（页面/片段/增量页共用）。返回 (ctx, Page)。"""
    page, crumbs = _listing(request, path, cat, sort, offset=offset, q=q)
    ctx = {
        "path": path, "cat": cat, "sort": sort, "view": view, "q": q,
        "entries": page.items, "crumbs": crumbs,
        "total": page.total, "offset": page.offset,
        "has_more": page.has_more, "next_offset": page.next_offset,
        "more_qs": _qs(path, cat, sort, view, page.next_offset, q),
        "is_admin": user["role"] == "admin",
        "ws_root_name": Path(ws.root()).name,
    }
    return ctx, page


@router.get("")
def files_page(request: Request, user: User, path: str = "", cat: str = "",
               sort: str = "name", view: str = "grid", q: str = "", offset: int = 0):
    status = ws.workspace_status()
    ctx = {
        "nav_active": "workbench",
        "ws_status": status,
        "path": path, "cat": cat, "sort": sort, "view": view, "q": q,
        "crumbs": [], "entries": [], "inspect": None,
        "counts": [], "locs": [],
        "total": 0, "offset": 0, "has_more": False, "next_offset": 0,
        "more_qs": "", "here": "",
        "is_admin": user["role"] == "admin",
        "ws_root_name": Path(status.get("root", "") or "hermes-workspace").name,
    }
    if status.get("error") or not status.get("exists"):
        return render(request, "files.html", ctx)
    try:
        view_ctx, _page = _view_ctx(request, user, path, cat, sort, view, q, offset)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path or cat or "/")
        return render(request, "files.html", ctx)
    ctx.update(view_ctx)
    ctx["here"] = f"{request.url.path}?{_qs(path, cat, sort, view, ctx['offset'], q)}"
    # 首屏延迟侧栏计数：链接恒在（buckets / 集合 key 是常量），数字经 /files/side
    # 异步补 —— 不再等全树索引把首屏卡死（0.8.34 残留）。
    ctx["locs"] = [(b, None) for b in ws.BUCKETS]
    ctx["counts"] = [(k, lbl, emj, None)
                     for k, (lbl, emj, _) in ws.CATEGORY_MAP.items()]
    ws.prefetch_index()   # 后台预热全树索引：非阻塞，后续视图/集合命中
    ctx["inspect"] = ws.inspect_summary()
    audit.record("files_browse", username=user["username"],
                 target=f"{path or cat or '/'}" + (f"（搜索 {q.strip()}）" if q.strip() else ""),
                 ip=client_ip(request))
    return render(request, "files.html", ctx)


def _listing(request: Request, path: str, cat: str, sort: str,
             offset: int = 0, q: str = ""):
    """五种视图模式：目录 / 智能集合 / 最近 / 回收站 / 搜索。返回 (Page, crumbs)。

    单页只返 ws.PAGE_SIZE 条：卡片本身轻，但每张带图片的卡片都是一个缩略图
    请求（首次还要服务端解码原图），一次送 1000 张就是把账单开给线上小机器。"""
    if q.strip():
        return (ws.paginate(ws.search(q), offset),
                [(f"搜索「{q.strip()}」", f"?q={q.strip()}")])
    if cat == "recent":
        return ws.paginate(ws.list_recent(), offset), [("最近使用", "?cat=recent")]
    if cat == "trash":
        return ws.paginate(ws.trash_list(), offset), [("回收站", "?cat=trash")]
    if cat:
        if cat not in ws.CATEGORY_MAP:
            raise ws.WorkspaceError(f"未知集合：{cat}")
        label = ws.CATEGORY_MAP[cat][0]
        return (ws.paginate(ws.list_collection(cat, sort=sort), offset),
                [(f"{label}（智能集合）", f"?cat={cat}")])
    return ws.paginate(ws.list_dir(path, sort), offset), ws.breadcrumbs(path)


@router.get("/list")
def list_fragment(request: Request, user: User, path: str = "", cat: str = "",
                  sort: str = "name", view: str = "grid", q: str = "", offset: int = 0):
    """HTMX 片段（兼容保留）；页面导航主走 hx-boost 整页。"""
    try:
        ctx, _page = _view_ctx(request, user, path, cat, sort, view, q, offset)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path or cat or "/")
        return
    return render_partial(request, "files/_content.html", ctx)


@router.get("/side")
def side_fragment(request: Request, user: User, path: str = "", cat: str = "",
                  sort: str = "name", view: str = "grid"):
    """侧栏片段：异步加载真实计数（首屏占位之后才补数）。

    与首页不同，片段可以安心等待索引（单飞收敛）——它是次请求、可缺省，不会卡住
    首屏。写操作后的 _side_oob 仍用它同款模板，计数实时刷新。
    """
    return render_partial(request, "files/_side.html", {
        "cat": cat, "path": path, "sort": sort, "view": view,
        "locs": ws.location_counts(), "counts": ws.category_counts(),
    })


@router.get("/more")
def more_fragment(request: Request, user: User, path: str = "", cat: str = "",
                  sort: str = "name", view: str = "grid", q: str = "",
                  offset: int = 0):
    """「加载更多」的下一页卡片（纯条目片段，不含网格容器）。

    前端 app.js wbMore() 用 fetch 拿这段 HTML 插到按钮前：响应可能有多个顶层
    节点，交给 htmx 换 outerHTML 不稳当，自己插最确定。X-Osfm-More / X-Osfm-Offset
    把翻页状态回给前端，前端不需要自己算。"""
    try:
        ctx, page = _view_ctx(request, user, path, cat, sort, view, q, offset)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path or cat or "/")
        return
    body = render_partial(request, "files/_more.html", ctx).body
    return Response(body, media_type="text/html; charset=utf-8",
                    headers={"X-Osfm-More": "1" if page.has_more else "0",
                             "X-Osfm-Offset": str(page.next_offset),
                             "Cache-Control": "no-cache"})


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


@router.get("/thumb")
def thumb(request: Request, user: User, path: str = ""):
    """网格缩略图（320px JPEG，磁盘缓存键含 mtime → 文件一变自动换新键）。

    不记审计——同 /files/raw 的浏览语义，别让一个照片目录刷几十条 INSERT；
    URL 内容永不变化（变化即换键），浏览器可放心长缓存。生成失败（非图片/
    已损坏）按 404 处理，前端 onerror 回退类型图标。"""
    try:
        p = ws.thumb_for(path)
    except ws.WorkspaceError as exc:
        _fail(request, exc, path)
    return FileResponse(p, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=604800, immutable"})


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
    """写操作后的统一收尾：原地重渲染当前视图（条目即时消失/出现）+ 侧栏 OOB。

    `here` 带上 offset（第几页），重渲染留在当前页；集合变短后越界的 offset
    由 ws.paginate 钳回首页，不会给用户一个空页。"""
    q = dict(parse_qsl(here, keep_blank_values=True)) if here else {}
    v_path, v_cat, v_q = q.get("path", ""), q.get("cat", ""), q.get("q", "")
    v_sort = q.get("sort", "name")
    v_view = q.get("view", "grid")
    try:
        v_offset = max(0, int(q.get("offset") or 0))
    except ValueError:
        v_offset = 0
    if v_sort not in ("name", "time", "size"):
        v_sort = "name"
    if v_view not in ("grid", "list"):
        v_view = "grid"
    admin = {"role": "admin"}
    try:
        ctx, _page = _view_ctx(request, admin, v_path, v_cat, v_sort, v_view, v_q, v_offset)
    except ws.WorkspaceError:
        parent = v_path.rsplit("/", 1)[0] if "/" in v_path else ""
        page = ws.paginate(_list_or_404(request, parent), v_offset)
        ctx = {
            "entries": page.items, "path": parent, "cat": "", "sort": v_sort,
            "view": v_view, "q": "", "crumbs": ws.breadcrumbs(parent),
            "total": page.total, "offset": page.offset, "has_more": page.has_more,
            "next_offset": page.next_offset,
            "more_qs": _qs(parent, "", v_sort, v_view, page.next_offset),
            "is_admin": True, "ws_root_name": Path(ws.root()).name,
        }
    body = render_partial(request, "files/_content.html", ctx).body
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
#
# 0.8.34 重写为 async：原来是同步端点，Starlette 会把同步生成器整个生命周期
# 绑在一个 worker 线程上——一个页面开 30 分钟 = 占 1 个线程，几个标签页就把
# 线程池耗光（线上表现：文件页一开，其他页面全部进不去）。现在只 sleep 不占
# 线程，扫盘这种真正阻塞的动作走一个专用小线程池，并用令牌限制并发个数。
# ---------------------------------------------------------------------------

WATCH_INTERVAL = 3.0
WATCH_MAX_SECONDS = 1800
# 目录签名只取前 N 项（排序后）：超大目录靠“项数 + 头部指纹”就足以发现变化。
WATCH_SIGNATURE_MAX = 2000
_WATCH_SLOTS: dict = {}   # id(event loop) -> [当前占用数]，单线程读写，不需锁


def _watch_slots() -> list:
    """每个 event loop 一份 SSE 占用计数（多 worker 部署时各自限流）。"""
    return _WATCH_SLOTS.setdefault(id(asyncio.get_running_loop()), [0])


def watch_signature(path: str = "", cat: str = ""):
    """目录签名（盯盘变更检测用），故意不走列表/索引缓存：盯的就是盘上真变化。

    只用 scandir 的 (名字, 是否目录, mtime)，不建 Entry 也不算目录子项数；条目数
    封顶 —— 盯一个 5 万文件的目录不该每 3 秒把全目录 stat 一遍。
    返回 None 表示目录不可读（前端不重发，下一轮再看）。"""
    if cat == "trash":
        target = ws.root() / ws.TRASH_DIR
    else:
        try:
            target = ws.resolve_rel(path)
        except ws.WorkspaceError:
            return None
    if not target.is_dir():
        return None
    sig = []
    try:
        with os.scandir(target) as it:
            for de in it:
                if de.name.startswith("."):
                    continue
                try:
                    mt = de.stat(follow_symlinks=False).st_mtime_ns
                except OSError:
                    mt = -1
                try:
                    is_dir = de.is_dir()
                except OSError:
                    is_dir = False
                sig.append((de.name, is_dir, mt))
    except OSError:
        return None
    sig.sort()
    return (len(sig), sig[:WATCH_SIGNATURE_MAX])


@router.get("/watch")
async def watch(request: Request, user: User, path: str = "", cat: str = ""):
    import json

    from fastapi.responses import StreamingResponse

    def _payload(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    if cat not in ("", "trash"):
        # 聚合视图无固定目录可盯：直接收尾，前端不会重连这个视图
        return StreamingResponse(iter([_payload("bye", {})]),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    slot = _watch_slots()
    max_slots = max(1, settings.workbench_sse_max)

    async def _agen():
        if slot[0] >= max_slots:
            # 监听名额满了：让前端退避重试（app.js 收到 busy 会 60s 后重接），
            # 而不是每 3 秒重连一次把服务敲在尖上
            yield _payload("busy", {"after": 60})
            return
        slot[0] += 1
        last = None
        first = True
        deadline = time.monotonic() + WATCH_MAX_SECONDS
        try:
            while time.monotonic() < deadline:
                if await request.is_disconnected():
                    break
                try:
                    sig = await anyio.to_thread.run_sync(lambda: watch_signature(path, cat))
                except ws.WorkspaceError:
                    sig = None
                if first:
                    yield _payload("hello", {})
                    first = False
                elif sig != last and last is not None:
                    # 盘上变了 → 列表/索引缓存必须跟着作废：不然前端收到
                    # changed 去重拉，拿到的还是最多 30s 前的旧快照（“活了但没变”）。
                    ws.invalidate_caches()
                    yield _payload("changed", {})
                last = sig
                await asyncio.sleep(WATCH_INTERVAL)
            yield _payload("bye", {})
        finally:
            slot[0] -= 1

    return StreamingResponse(_agen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
