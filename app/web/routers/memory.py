"""记忆系统路由：状态总览、内置调优、外置方案选择与自动配置。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from app.core import audit
from app.hermes import memory_service as mem
from app.hermes.memory_service import MemoryError
from app.hermes.paths import detect
from app.web.deps import User, client_ip, csrf_guard
from app.web.htmx import is_htmx, redirect, toast
from app.web.templating import render, render_partial

# csrf_guard：与其余路由对齐（此前缺失，仅靠 samesite=strict 兜底——防纵深要一致）
router = APIRouter(prefix="/memory", dependencies=[Depends(csrf_guard)])


@router.get("")
def memory_page(request: Request, user: User):
    paths = detect()
    st = mem.get_status(paths)
    detected = [name + ".json" for name, exists in st.config_files.items() if exists]
    return render(request, "memory.html", {
        "nav_active": "memory",
        "status": st,
        "providers": mem.list_providers(),
        "builtin_presets": mem.BUILTIN_PRESETS,
        "paths": paths,
        "active_job_kind": _active_job_kind(),
        "detected_files": detected,
    })


def _active_job_kind() -> str:
    from app.hermes import installer

    job = installer.active_job()
    return job["kind"] if job else ""


@router.get("/provider/{pid}")
def provider_form(request: Request, user: User, pid: str):
    try:
        d = mem.get_provider_def(pid)
    except MemoryError:
        return _reject(request, str(MemoryError(f"未知记忆方案：{pid}")), 404)
    st = mem.get_status()
    return render_partial(request, "memory/_provider_form.html", {
        "d": d,
        "status": st,
    })


@router.post("/builtin")
def save_builtin(request: Request, user: User,
                 memory_enabled: str = Form("off"),
                 user_profile_enabled: str = Form("off"),
                 write_approval: str = Form("off"),
                 memory_char_limit: str = Form("2200"),
                 user_char_limit: str = Form("1375"),
                 nudge_interval: str = Form("10"),
                 context_file_max_chars: str = Form("")):
    try:
        changes = mem.save_builtin(
            memory_enabled=memory_enabled == "on",
            user_profile_enabled=user_profile_enabled == "on",
            write_approval=write_approval == "on",
            memory_char_limit=_int(memory_char_limit, 2200),
            user_char_limit=_int(user_char_limit, 1375),
            nudge_interval=_int(nudge_interval, 10),
            context_file_max_chars=_int(context_file_max_chars, 0) or None,
        )
    except MemoryError as exc:
        return _reject(request, str(exc))
    audit.record("memory_builtin_save", username=user["username"], detail=changes,
                 ip=client_ip(request))
    resp = redirect(request, "/memory")
    toast(resp, "内置记忆配置已保存（config.yaml memory.*）")
    return resp


@router.post("/apply/{pid}")
async def apply_provider(request: Request, user: User, pid: str):
    try:
        d = mem.get_provider_def(pid)
    except MemoryError as exc:
        return _reject(request, str(exc), 404)
    form = await request.form()
    values = {k: v for k, v in form.items() if k != "_csrf"}
    try:
        changes = mem.apply_provider(pid, values)
    except MemoryError as exc:
        return _reject(request, str(exc))
    audit.record("memory_provider_apply", username=user["username"], target=pid,
                 detail=changes, ip=client_ip(request))
    resp = redirect(request, "/memory")
    toast(resp, f"{d.label} 已启用：{len(changes)} 项配置写入完成")
    return resp


@router.post("/disable")
def disable(request: Request, user: User, remove_mcp: str = Form("on")):
    try:
        changes = mem.disable_provider(keep_mcp=remove_mcp != "on")
    except MemoryError as exc:
        return _reject(request, str(exc))
    audit.record("memory_provider_disable", username=user["username"],
                 detail=changes, ip=client_ip(request))
    resp = redirect(request, "/memory")
    toast(resp, "已回到纯内置记忆")
    return resp


@router.post("/install-job/{pid}")
async def install_job(request: Request, user: User, pid: str):
    from app.hermes import installer

    try:
        d = mem.get_provider_def(pid)
        form = await request.form()
        values = {k: str(v) for k, v in form.items() if k != "_csrf"}
        mem.submit_install_job(pid, values)
    except MemoryError as exc:
        return _reject(request, str(exc))
    except installer.JobBusy as exc:
        return _reject(request, str(exc))
    audit.record("memory_install_job", username=user["username"], target=pid,
                 detail=d.job_label, ip=client_ip(request))
    resp = redirect(request, "/memory")
    toast(resp, d.job_label + "：任务已提交")
    return resp


@router.post("/test/{pid}")
async def test_provider(request: Request, user: User, pid: str):
    """连接测试：用表单当前值（未保存也行，缺失回落 .env）探测端点可达性与密钥有效性。"""
    try:
        d = mem.get_provider_def(pid)
    except MemoryError as exc:
        return _reject(request, str(exc), 404)
    form = await request.form()
    values = {k: str(v) for k, v in form.items() if k != "_csrf"}
    result = mem.test_connection(pid, values)
    audit.record("memory_provider_test", username=user["username"], target=pid,
                 outcome=result["level"], detail=result["message"][:300],
                 ip=client_ip(request))
    return render_partial(request, "memory/_test_result.html",
                          {"d": d, "result": result},
                          status_code=200 if result["level"] == "ok" else 502)


@router.get("/job")
def job_fragment(request: Request, user: User):
    from app.core import appsettings
    from app.hermes import installer

    job = installer.active_job() or installer.last_job()
    lines = installer.job_log(job["id"]) if job else []
    return render_partial(request, "memory/_job_panel.html", {
        "job": job, "job_lines": lines,
        "done": job is None or job["status"] != "running",
        "restart_pending": bool(appsettings.get_setting("plugin_restart_pending")),
    })


def _int(raw: str, default: int) -> int:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else default


def _reject(request: Request, message: str, code: int = 400):
    from fastapi.responses import HTMLResponse

    if is_htmx(request):
        return HTMLResponse(f"<div class='alert alert-danger'>{message}</div>",
                            status_code=code)
    return render(request, "error.html",
                  {"nav_active": "memory", "code": code, "message": message},
                  status_code=code)
