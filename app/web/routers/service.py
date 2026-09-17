"""服务管理：Hermes 安装 / 更新、Gateway 启停、日志与任务输出。"""
from __future__ import annotations

import sys

from fastapi import APIRouter, Depends, Form, Request

from app.core import audit
from app.core.settings import settings
from app.hermes import installer, supervisor
from app.hermes.paths import detect, resolve_agent_repo
from app.web.deps import Admin, User, client_ip, csrf_guard
from app.web.htmx import is_htmx, toast
from app.web.templating import render, render_partial

router = APIRouter(prefix="/service", dependencies=[Depends(csrf_guard)])

DANGER_WORDS = {"stop": "停止", "restart": "重启"}


@router.get("")
def service_page(request: Request, user: User):
    return _service_view(request)


def _job_panel_ctx() -> dict:
    """任务面板上下文（整页与 HTMX 片段共用；_job_panel.html 需要 job/job_lines/done）。
    最近一次任务的面板常驻展示——任务跑完就消失曾导致"操作完日志找不到"。"""
    job = installer.active_job() or installer.last_job()
    log_path = ""
    if job and job.get("log_path"):
        try:
            log_path = str((settings.data_dir / job["log_path"]).resolve())
        except OSError:
            log_path = ""
    return {
        "active_job": installer.active_job(),
        "last_job": installer.last_job(),
        "job": job,
        # 全量输出（安装日志一两百行）：用户要看见进度，不能只给尾巴
        "job_lines": installer.job_log(job["id"], tail=500) if job else [],
        "done": job is None or job["status"] != "running",
        "job_log_path": log_path,
    }


def _service_view(request: Request, error: str = "", code: int = 200,
                  show_force_install: bool = False, notice: str = ""):
    paths = detect()
    st = supervisor.status(paths)
    return render(request, "service.html", {
        "nav_active": "service",
        "status": st,
        "paths": paths,
        "version": supervisor.version(paths),
        "gateway_log": supervisor.tail_log(paths.gateway_log, 120),
        "errors_log": supervisor.tail_log(paths.errors_log, 60),
        "install_cmd": installer.INSTALL_CMD,
        "install_method": installer.INSTALL_METHOD_LABEL,
        "agent_repo_dir": str(resolve_agent_repo(paths) or paths.agent_repo),
        "jobs": installer.job_history(),
        "error": error,
        "notice": notice,
        "show_force_install": show_force_install,
        # 安装完整性逐项体检：装没装完，如实呈现（不再用"二进制存在"糊弄）
        "install_health": installer.install_health(paths),
        **_job_panel_ctx(),
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
    """start / stop / restart：全部转为后台任务——输出实时进终端 + 任务面板 + 日志文件，
    可取消、可回查；页面即时返回不再"卡死"，且每一步都有流水可看。
    （此前是同步调用：阻塞页面且没有任何日志，实机踩坑。）"""
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

    label = {"start": "启动", "stop": "停止", "restart": "重启"}[action]
    try:
        installer.submit(f"gateway_{action}",
                         installer.gateway_action_job(action),
                         cwd=str(settings.jobs_dir))
    except installer.JobBusy as exc:
        audit.record(f"gateway_{action}", username=username, outcome="failed",
                     detail=str(exc), ip=ip)
        return _reject(request, str(exc))

    audit.record(f"gateway_{action}", username=username,
                 detail="已提交后台任务", ip=ip)
    return _after_change(
        request, paths,
        f"已提交「{label} Gateway」任务——输出见页面顶部任务面板（终端同步打印）")


@router.post("/install")
def install(request: Request, user: Admin, force: str = Form("")):
    """官方源全量安装（不跳过任何组件）。点击即提交任务——**不做预检拦截**：
    执行与否、成败原因，全在任务日志里实时可见（页顶任务面板 / 终端 / jobs 文件）。"""
    chosen = installer.preferred_install()
    return _submit_job(request, user, "install", chosen["cmd"],
                       f"开始安装 Hermes Agent（{chosen['label']}）",
                       force=bool(force.strip()))


@router.post("/browser")
def browser_backfill(request: Request, user: Admin):
    """补装浏览器组件：官方源优先、国内镜像兜底（见 installer.browser_install_job）。"""
    command = installer.browser_install_job()
    if command is None:
        return _reject(request, "未找到 Hermes 安装目录（或平台不支持），请先完成 Hermes 安装")
    return _submit_job(request, user, "browser_install", command,
                       "开始补装浏览器组件")


@router.post("/job/cancel")
def cancel_job(request: Request, user: Admin, job_id: int = Form(...)):
    result = installer.cancel(job_id)
    audit.record("job_cancel", username=user["username"],
                 detail=f"job={job_id} {result}", ip=client_ip(request))
    return _after_change(request, detect(), result)


@router.post("/update")
def update(request: Request, user: Admin):
    return _submit_job(request, user, "update", installer.UPDATE_CMD, "开始更新 Hermes Agent")


def _submit_job(request: Request, user, kind: str, command: str, message: str,
                force: bool = False):
    """提交后台任务。不做预检拦截：必败任务也会先跑起来并把真实错误写进日志
    （curl/pip 的报错就是最好的诊断），不再让"点了没反应"发生。"""
    paths = detect()
    if force:
        message = message + "（手动触发）"
    try:
        # 工作目录固定为 jobs_dir：此前继承控制台进程目录（即仓库根），
        # 曾出现子进程杂物（如 PowerShell 模块缓存 Microsoft/）落到仓库里。
        # 官方安装器全用绝对路径，换目录不影响安装结果。
        installer.submit(kind, command, cwd=str(settings.jobs_dir))
    except installer.JobBusy as exc:
        return _reject(request, str(exc))
    audit.record(f"job_{kind}", username=user["username"], detail=command,
                 ip=client_ip(request))
    return _after_change(request, paths, message)


@router.get("/job")
def job_fragment(request: Request, user: User):
    return render_partial(request, "service/_job_panel.html", _job_panel_ctx())


def _job_stream_payloads():
    """SSE 事件流（生成器）：job 元信息 → lines 日志增量 → done 收尾。

    抽成独立生成器便于单测；真实 HTTP 推流由 /service/job/stream 包装。"""
    import json
    import time as _time

    def _payload(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    last_job_id = None
    offset = 0
    deadline = _time.monotonic() + 1800
    while True:
        if _time.monotonic() > deadline:
            yield _payload({"type": "bye"})
            return
        job = installer.active_job() or installer.last_job()
        if job is None:
            yield _payload({"type": "idle"})
            _time.sleep(1.5)
            continue
        if job["id"] != last_job_id:
            last_job_id = job["id"]
            offset = 0
            yield _payload({
                "type": "job",
                "id": job["id"],
                "kind": job["kind"],
                "status": job["status"],
                "label": installer.JOB_KIND_LABELS.get(job["kind"], "后台任务"),
                "command": job["command"],
            })
        lines, offset = installer.job_log_delta(job["id"], offset)
        if lines:
            yield _payload({"type": "lines", "lines": lines})
        if job["status"] != "running":
            yield _payload({"type": "done", "status": job["status"],
                            "exit_code": job["exit_code"]})
            return
        _time.sleep(0.4)


@router.get("/job/stream")
def job_stream(request: Request, user: User):
    """SSE 实时推送：新行毫秒级到达（替代前端轮询）；任务结束即收尾关闭。"""
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        _job_stream_payloads(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/jobs/history")
def jobs_history_fragment(request: Request, user: User):
    """历史列表片段（运行中时每 3s 轮询，结束后定格为可回看的台账）。"""
    return render_partial(request, "service/_jobs_history.html", {
        "jobs": installer.job_history(),
    })


@router.get("/jobs/{job_id}/log")
def job_log_fragment(request: Request, user: User, job_id: int):
    return render_partial(request, "service/_job_log_lines.html", {
        "job_lines": installer.job_log(job_id, tail=200),
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

def _reject(request: Request, message: str, code: int = 400,
            show_force_install: bool = False):
    if is_htmx(request):
        from fastapi.responses import HTMLResponse

        return HTMLResponse(
            f"<div class='alert alert-danger'>{message}</div>", status_code=code)
    return _service_view(request, error=message, code=code,
                         show_force_install=show_force_install)


def _after_change(request: Request, paths, message: str):
    if is_htmx(request):
        resp = render_partial(request, "service/_status_brief.html", {
            "status": supervisor.status(paths), "paths": paths,
            "version": supervisor.version(paths),
        })
        toast(resp, message)
        resp.headers["HX-Push-Url"] = "false"
        return resp
    return _service_view(request, notice=message)
