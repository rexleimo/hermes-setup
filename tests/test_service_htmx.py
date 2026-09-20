"""服务页 HTMX 局部刷新：动作按钮不再整页提交。

背景：此前服务页所有动作（启停/安装/更新/补装/取消）都是原生表单提交——
每次操作整页跳转刷新，任务日志、页签位置、滚动位置全部丢失。现在动作走
HTMX：响应只换任务面板 + 状态区即时刷新（svc-refresh 事件）+ toast；
失败时错误提示与任务面板一起换入（不顶掉日志）。以下用例锁死这些契约。
"""
from __future__ import annotations

import json
import re



def _csrf(client) -> str:
    page = client.get("/service")
    m = re.search(r'name="_csrf" value="([^"]*)"', page.text)
    return m.group(1)


def test_service_page_uses_htmx_actions(logged_in):
    text = logged_in.get("/service").text
    # 动作表单全部带 hx-post（局部提交）。update/browser 仅在已安装状态渲染，
    # 未安装页（测试环境）必有 action/install。
    for action in ("/service/action", "/service/install"):
        assert f'hx-post="{action}"' in text
    # 页面里出现的每个 post 表单都不能是"裸"表单（都应有局部提交通道）
    import re as _re
    for m in _re.finditer(r'<form[^>]*action="(/[^"]+)"[^>]*>', text):
        assert "hx-post" in m.group(0) or m.group(1) in ("/logout",), \
            f"表单 {m.group(1)} 缺少 hx-post"
    assert 'hx-target="#job-panel"' in text
    # 状态区：轮询 + 动作成功后的即时刷新事件
    assert "svc-refresh from:body" in text


def test_action_submit_swaps_job_panel_not_page(logged_in, monkeypatch):
    """HTMX 提交网关动作：200 + 任务面板片段 + toast + svc-refresh 头。"""
    from app.hermes import installer

    calls = {}
    monkeypatch.setattr(installer, "submit",
                        lambda kind, cmd, **kw: calls.update(kind=kind) or 1)
    token = _csrf(logged_in)
    resp = logged_in.post("/service/action",
                          data={"action": "start", "_csrf": token},
                          headers={"HX-Request": "true"},
                          follow_redirects=False)
    assert resp.status_code == 200
    assert calls["kind"] == "gateway_start"
    body = resp.text
    assert 'id="job-panel"' in body          # 换入的是任务面板片段
    assert "<!DOCTYPE" not in body           # 不是整页
    trigger = json.loads(resp.headers["HX-Trigger"])
    assert "console:toast" in trigger
    assert trigger.get("svc-refresh") is True  # 状态区即时刷新


def test_action_reject_keeps_job_panel(logged_in):
    """HTMX 提交被拒（确认词不匹配）：错误提示与任务面板一起返回，
    面板不会被 alert 顶掉。"""
    token = _csrf(logged_in)
    resp = logged_in.post("/service/action",
                          data={"action": "stop", "confirm": "错的",
                                "_csrf": token},
                          headers={"HX-Request": "true"},
                          follow_redirects=False)
    assert resp.status_code == 400
    assert "alert-danger" in resp.text
    assert 'id="job-panel"' in resp.text


def test_non_htmx_submit_still_full_page(logged_in, monkeypatch):
    """无 HTMX 头（原生提交/降级）：保持整页渲染，不破坏老路径。"""
    from app.hermes import installer

    monkeypatch.setattr(installer, "submit", lambda kind, cmd, **kw: 1)
    token = _csrf(logged_in)
    resp = logged_in.post("/service/action",
                          data={"action": "start", "_csrf": token},
                          follow_redirects=False)
    assert resp.status_code == 200
    assert "<!DOCTYPE" in resp.text


def test_installed_state_page_renders(logged_in, hermes_home, monkeypatch):
    """已安装状态页（更新/完整性/补装卡片）能正常渲染，不报模板错误。"""
    import dataclasses

    from app.hermes import paths as paths_module

    home = hermes_home.home
    (home / "bin").mkdir(parents=True, exist_ok=True)
    exe = home / "bin" / "hermes"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    paths_module.set_override(dataclasses.replace(hermes_home, bin=str(exe)))
    page = logged_in.get("/service")
    assert page.status_code == 200
    assert 'hx-post="/service/update"' in page.text
    assert 'hx-post="/service/browser"' in page.text
    assert 'hx-post="/service/install"' in page.text   # 修复安装


def test_app_js_resubscribes_sse_after_swap():
    """任务面板被 HTMX 换入后，SSE 必须重新挂载（否则实时日志静默丢失）。"""
    from pathlib import Path

    js = Path("app/web/static/js/app.js").read_text(encoding="utf-8")
    assert "function attachJobStream" in js
    assert 'addEventListener("htmx:afterSwap", attachJobStream)' in js
    # 确认弹窗对 HTMX 表单走 htmx.ajax（form.submit 不触发 htmx）
    assert "htmx.ajax(\"POST\", form.getAttribute(\"hx-post\")" in js
