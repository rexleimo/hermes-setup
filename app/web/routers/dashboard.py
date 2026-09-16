"""仪表盘：初始化进度、服务状态、资产概览。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core import audit
from app.hermes import supervisor
from app.hermes.paths import detect
from app.hermes.channels_service import list_channels
from app.hermes.providers_service import get_fallback, list_providers
from app.web.deps import User, csrf_guard
from app.web.templating import render

router = APIRouter(dependencies=[Depends(csrf_guard)])


@router.get("/")
def dashboard(request: Request, user: User):
    paths = detect()
    st = supervisor.status(paths)
    providers = list_providers()
    channels = list_channels()
    fallback = get_fallback()

    main = next((p for p in providers if p.is_main), None)
    enabled_channels = [c for c in channels if c.enabled]

    checklist = [
        {"key": "install", "label": "安装 Hermes Agent",
         "done": paths.installed,
         "desc": "未安装时可前往「服务管理」一键执行官方安装脚本",
         "href": "/service"},
        {"key": "provider", "label": "添加模型供应商并设为主模型",
         "done": main is not None,
         "desc": "先有模型，Hermes 才能思考", "href": "/providers"},
        {"key": "channel", "label": "启用至少一个消息渠道",
         "done": len(enabled_channels) > 0,
         "desc": "飞书 / 微信 / QQ / Telegram 等，随时随地给它发指令",
         "href": "/channels"},
        {"key": "gateway", "label": "启动 Gateway 服务",
         "done": st.running is True,
         "desc": "网关负责接收各渠道消息并驱动 Agent", "href": "/service"},
    ]

    return render(request, "dashboard.html", {
        "nav_active": "dashboard",
        "status": st,
        "providers": providers,
        "channels": channels,
        "enabled_channel_count": len(enabled_channels),
        "main_provider": main,
        "fallback": fallback,
        "checklist": checklist,
        "check_done": sum(1 for c in checklist if c["done"]),
        "audit_rows": audit.recent(8),
        "version": supervisor.version(paths),
        "paths": paths,
    })
