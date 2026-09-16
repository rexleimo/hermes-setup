"""消息渠道（platforms）路由：列表卡片、单渠道编辑、启停、工具集覆盖。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from app.core import audit
from app.hermes import onboarding
from app.hermes.channels_service import (
    ChannelError, get_channel, list_channels, save_channel, set_toolset,
)
from app.hermes.schema import (
    PLATFORMS, PLATFORM_TOOLSET_KEYS, TOOLSET_CHOICES, TOOLSET_PRESETS,
)
from app.web.deps import Admin, User, client_ip, csrf_guard
from app.web.htmx import is_htmx, redirect, toast
from app.web.templating import render, render_partial

router = APIRouter(prefix="/channels", dependencies=[Depends(csrf_guard)])


@router.get("")
def list_page(request: Request, user: User):
    views = list_channels()
    enabled = [v for v in views if v.enabled]
    return render(request, "channels/list.html", {
        "nav_active": "channels",
        "channels": views,
        "enabled_count": len(enabled),
    })


@router.get("/{name}")
def edit_page(request: Request, user: User, name: str):
    try:
        view = get_channel(name)
    except ChannelError:
        return _not_found(request)
    import json as _json

    return render(request, "channels/detail.html", {
        "nav_active": "channels",
        "c": view,
        "def": view.definition,
        "toolset_choices": TOOLSET_PRESETS + TOOLSET_CHOICES,
        "platform_keys": list(PLATFORM_TOOLSET_KEYS),
        "f_has_cred": any(f.required for f in view.definition.extra_fields),
        "advanced_extra": _json.dumps(view.extra, ensure_ascii=False, indent=2, sort_keys=True)
                          if view.extra else "{ }",
    })


@router.post("/{name}/save")
async def save(request: Request, user: User, name: str):
    """从表单收集 env / config / extra 字段并应用。"""
    d = PLATFORMS.get(name)
    if d is None:
        return _not_found(request)

    form = await request.form()
    # 未出现 = 不改动；出现且为 "on" = 启用；出现且为 "off" = 停用
    enabled_raw = form.get("enabled")
    if "enabled" in form:
        enabled = enabled_raw == "on"
    else:
        enabled = None
    env_values = {f.name: _last(form, f"env_{f.name}") for f in d.env_fields}
    config_values = {f.name: _last(form, f"cfg_{f.name}") for f in d.config_keys}
    extra_values = {f.name: _last(form, f"ext_{f.name}") for f in d.extra_fields}
    extra_json = _last(form, "advanced_extra")
    current_json = _render_extra_json(name)
    if extra_json.strip() == current_json.strip():
        extra_json = None  # 未改动

    try:
        changes = save_channel(name, enabled=enabled,
                               env_values=env_values, config_values=config_values,
                               extra_values=extra_values, extra_json=extra_json)
    except ChannelError as exc:
        return _reject(request, str(exc))

    audit.record("channel_save", username=user["username"], target=name,
                 detail=changes, ip=client_ip(request))
    resp = redirect(request, f"/channels/{name}")
    toast(resp, "渠道配置已保存并写入 config.yaml / .env")
    return resp


@router.post("/{name}/toggle")
def toggle(request: Request, user: User, name: str, enable: str = Form(...)):
    try:
        changes = save_channel(name, enabled=enable == "1")
    except ChannelError as exc:
        return _reject(request, str(exc))
    audit.record("channel_toggle", username=user["username"], target=name,
                 detail=f"{'启用' if enable == '1' else '停用'}", ip=client_ip(request))
    if is_htmx(request):
        view = get_channel(name)
        resp = render_partial(request, "channels/_card.html", {"c": view})
        toast(resp, f"{view.label} 已{'启用' if view.enabled else '停用'}")
        return resp
    resp = redirect(request, "/channels")
    toast(resp, "渠道状态已更新")
    return resp


@router.post("/{name}/toolset")
def toolset(request: Request, user: User, name: str, choice: str = Form("")):
    try:
        message = set_toolset(name, choice.strip())
    except ChannelError as exc:
        return _reject(request, str(exc))
    audit.record("channel_toolset", username=user["username"], target=name,
                 detail=choice or "(清除覆盖)", ip=client_ip(request))
    resp = redirect(request, f"/channels/{name}")
    toast(resp, message)
    return resp


# ---------------------------------------------------------------------------
# 接入助手（onboarding）：浏览器内扫码 + 一键装依赖 + 自动回填（weixin 先行）


@router.get("/{name}/onboard")
def onboard_panel(request: Request, user: User, name: str):
    """HTMX 轮询片段：依赖状态 + 二维码 + 进度。"""
    if name != "weixin":
        return _not_found(request)
    return _panel(request, user, err=None)


@router.post("/{name}/qr-start")
def qr_start(request: Request, user: Admin, name: str):
    if name != "weixin":
        return _not_found(request)
    try:
        onboarding.start_qr_login()
    except Exception as exc:
        return _panel(request, user, err=str(exc))
    audit.record("channel_qr_start", username=user["username"], target=name,
                 ip=client_ip(request))
    return _panel(request, user, err=None)


@router.post("/{name}/deps-install")
def deps_install(request: Request, user: Admin, name: str):
    try:
        onboarding.install_deps()
    except Exception as exc:
        return _panel(request, user, err=f"安装任务启动失败：{exc}")
    audit.record("channel_deps_install", username=user["username"], target=name,
                 ip=client_ip(request))
    return _panel(request, user, err=None)


def _panel(request: Request, user, *, err: str | None):
    state = onboarding.qr_state()
    acct = onboarding.current_account_id()
    if state["phase"] == "confirmed" and state["account_id"] and state["account_id"] != acct:
        try:
            changes = onboarding.apply_weixin_account(state["account_id"],
                                                      state.get("user_id") or "",
                                                      username=user["username"])
            acct = state["account_id"]
            if changes:
                # 每次扫码都会新起一个 iLink 会话并作废旧 token；运行中的 Gateway
                # 手里还是旧 token（Session expired 静默丢消息）。必须自动重启，
                # 不能指望小白知道要去服务页点重启。
                from app.hermes import supervisor
                try:
                    if supervisor.status().running:
                        supervisor.restart()
                        state["restarted"] = True
                except Exception:
                    state["restart_failed"] = True  # 面板提示手动重启
        except ChannelError as exc:
            err = err or f"自动回填失败：{exc}"
    return render_partial(request, "channels/_onboard.html", {
        "state": state,
        "deps": onboarding.deps_status(),
        "deps_job": onboarding.deps_job_state(),
        "acct": acct,
        "err": err,
        "polling": state["phase"] in ("starting", "qr", "scaned")
                   or bool(onboarding.deps_job_state() and
                           onboarding.deps_job_state()["status"] == "running"),
    })


# ---------------------------------------------------------------------------


def _render_extra_json(name: str) -> str:
    """当前 extra 的格式化 JSON（用于高级编辑初始值）。"""
    import json as _json

    try:
        v = get_channel(name)
        return _json.dumps(v.extra, ensure_ascii=False, indent=2, sort_keys=True) if v.extra else "{ }"
    except ChannelError:
        return "{ }"

def _last(form, key: str) -> str:
    """取表单字段的最后一个值（兼容 hidden off + checkbox on 的开关模式）。"""
    values = form.getlist(key)
    return str(values[-1]) if values else ""


def _not_found(request: Request):
    if is_htmx(request):
        from fastapi.responses import HTMLResponse

        return HTMLResponse("<div class='alert alert-danger'>渠道不存在</div>", status_code=404)
    return render(request, "error.html",
                  {"nav_active": "channels", "code": 404, "message": "渠道不存在"},
                  status_code=404)


def _reject(request: Request, message: str, code: int = 400):
    if is_htmx(request):
        from fastapi.responses import HTMLResponse

        return HTMLResponse(f"<div class='alert alert-danger'>{message}</div>", status_code=code)
    return render(request, "error.html",
                  {"nav_active": "channels", "code": code, "message": message},
                  status_code=code)
