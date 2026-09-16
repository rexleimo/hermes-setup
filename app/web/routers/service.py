"""服务管理：Hermes 安装 / 更新、Gateway 启停、日志与任务输出。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from app.core import audit
from app.hermes import installer, supervisor
from app.hermes.paths import detect
from app.web.deps import Admin, User, client_ip, csrf_guard
from app.web.htmx import is_htmx, toast
from app.web.templating import render, render_partial

router = APIRouter(prefix="/service", dependencies=[Depends(csrf_guard)])

DANGER_WORDS = {"stop": "停止", "restart": "重启"}


@router.get("")
def service_page(request: Request, user: User):
    return _service_view(request)


def _service_view(request: Request, error: str = "", code: int = 200):
    paths = detect()
    st = supervisor.status(paths)
    return render(request, "service.html", {
        "nav_active": "service",
        "status": st,
        "paths": paths,
        "version": supervisor.version(paths),
        "active_job": installer.active_job(),
        "last_job": installer.last_job(),
        "gateway_log": supervisor.tail_log(paths.gateway_log, 120),
        "errors_log": supervisor.tail_log(paths.errors_log, 60),
        "install_cmd": installer.INSTALL_CMD,
        "error": error,
        **_readiness(),
    }, status_code=code)


def _readiness() -> dict:
    """启动前检查：主模型与渠道是否就绪。"""
    from app.hermes.channels_service import list_channels
    from app.hermes.providers_service import list_providers

    try:
        main_ready = any(p.is_main for p in list_providers())
        channel_ready = any(c.enabled and c.configured for c in list_channels())
    except Exception:
        main_ready = channel_ready = False
    return {"main_ready": main_ready, "channel_ready": channel_ready}


@router.post("/action")
def service_action(request: Request, user: User, action: str = Form(...),
                   confirm: str = Form("")):
    """start / stop / restart；危险动作需在表单中输入动作名确认。"""
    paths = detect()
    ip = client_ip(request)
    username = user["username"]

    if action not in ("start", "stop", "restart"):
        return _reject(request, "未知操作")

    if action in DANGER_WORDS:
        if confirm.strip() != DANGER_WORDS[action]:
            audit.record(f"gateway_{action}", username=username, outcome="denied",
                         detail="确认词不匹配", ip=ip)
            return _reject(request, "请输入正确的确认词以继续")

    try:
        output = {"start": supervisor.start, "stop": supervisor.stop,
                  "restart": supervisor.restart}[action](paths)
    except supervisor.SupervisorError as exc:
        audit.record(f"gateway_{action}", username=username, outcome="failed",
                     detail=str(exc), ip=ip)
        if is_htmx(request):
            resp = render_partial(request, "service/_status_brief.html", {
                "status": supervisor.status(paths), "paths": paths,
                "version": supervisor.version(paths),
            }, status_code=502)
            toast(resp, f"{DANGER_WORDS.get(action, action)} 失败：{exc}", level="error")
            return resp
        return _service_view(request, error=str(exc), code=502)

    audit.record(f"gateway_{action}", username=username, detail=output[:300], ip=ip)
    return _after_change(request, paths, f"已{DANGER_WORDS.get(action, '执行')} Gateway")


@router.post("/install")
def install(request: Request, user: Admin):
    return _submit_job(request, user, "install", installer.INSTALL_CMD, "开始安装 Hermes Agent")


@router.post("/update")
def update(request: Request, user: Admin):
    return _submit_job(request, user, "update", installer.UPDATE_CMD, "开始更新 Hermes Agent")


def _submit_job(request: Request, user, kind: str, command: str, message: str):
    paths = detect()
    try:
        installer.submit(kind, command)
    except installer.JobBusy as exc:
        return _reject(request, str(exc))
    audit.record(f"job_{kind}", username=user["username"], detail=command,
                 ip=client_ip(request))
    return _after_change(request, paths, message)


@router.get("/job")
def job_fragment(request: Request, user: User):
    job = installer.active_job() or installer.last_job()
    lines = installer.job_log(job["id"]) if job else []
    done = job is None or job["status"] != "running"
    return render_partial(request, "service/_job_panel.html", {
        "job": job, "job_lines": lines, "done": done,
    })


@router.get("/logs")
def logs_fragment(request: Request, user: User, name: str = "gateway", lines: int = 120):
    paths = detect()
    path = paths.gateway_log if name == "gateway" else paths.errors_log
    log_lines = supervisor.tail_log(path, max(20, min(lines, 500)))
    return render_partial(request, "service/_log_view.html", {
        "log_lines": log_lines, "log_name": name,
    })


@router.get("/status")
def status_fragment(request: Request, user: User):
    paths = detect()
    return render_partial(request, "service/_status_brief.html", {
        "status": supervisor.status(paths), "paths": paths,
        "version": supervisor.version(paths),
    })


@router.get("/pill")
def pill_fragment(request: Request, user: User):
    paths = detect()
    return render_partial(request, "service/_topbar_pill.html", {
        "status": supervisor.status(paths),
    })


# ---------------------------------------------------------------------------

def _reject(request: Request, message: str, code: int = 400):
    if is_htmx(request):
        from fastapi.responses import HTMLResponse

        return HTMLResponse(
            f"<div class='alert alert-danger'>{message}</div>", status_code=code)
    return _service_view(request, error=message, code=code)


def _after_change(request: Request, paths, message: str):
    if is_htmx(request):
        resp = render_partial(request, "service/_status_brief.html", {
            "status": supervisor.status(paths), "paths": paths,
            "version": supervisor.version(paths),
        })
        toast(resp, message)
        resp.headers["HX-Push-Url"] = "false"
        return resp
    return _service_view(request)
