"""WebOS 窗口层（WI-9）：/files/viewer 描述符端点。

前端窗口管理器按 kind 查 Viewer 注册表构建视图；新增文件类型 =
workspace_service.preview_class 加分支 + 前端注册表加条目，本端点保持稳定。
"""
from __future__ import annotations

import base64

import pytest

from app.hermes import engineering_service as eng

# 1x1 PNG（最小有效图，供 image 分支用）
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


@pytest.fixture()
def workspace(logged_in, tmp_path):
    ws = tmp_path / "hermes-workspace"
    s = eng.EngSettings(workspace=str(ws))
    eng.save_settings(s)
    eng.init_workspace(s)
    return ws


def test_viewer_descriptor_kinds(workspace, logged_in):
    (workspace / "pictures" / "a.png").write_bytes(PNG_1PX)
    (workspace / "videos" / "a.mp4").write_bytes(b"\x00\x01fake mp4")
    (workspace / "downloads" / "a.mp3").write_bytes(b"\xff\xfebpm3")
    (workspace / "documents" / "a.pdf").write_bytes(b"%PDF-1.4 fake")
    (workspace / "documents" / "notes.txt").write_text("hello window", encoding="utf-8")
    (workspace / "documents" / "page.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (workspace / "downloads" / "app.exe").write_bytes(b"MZexe")

    cases = {
        "pictures/a.png": ("image", True),
        "videos/a.mp4": ("video", True),
        "downloads/a.mp3": ("audio", True),
        "documents/a.pdf": ("pdf", True),
        "documents/notes.txt": ("text", True),
        "documents/page.html": ("html", True),   # WI-19：沙盒预览，不再走 text
        "downloads/app.exe": ("none", False),   # .exe 与 WebOS 无关联 → 不支持
    }
    for rel, (kind, ok) in cases.items():
        r = logged_in.get("/files/viewer", params={"path": rel})
        assert r.status_code == 200, rel
        d = r.json()
        assert d["kind"] == kind, (rel, d)
        assert d["ok"] is ok, (rel, d)
        assert d["rel"] == rel

    # text 类型带内容
    d = logged_in.get("/files/viewer", params={"path": "documents/notes.txt"}).json()
    assert d["text"] == "hello window"
    assert d["truncated"] is False


def test_viewer_text_truncates_at_limit(workspace, logged_in):
    from app.hermes import workspace_service as ws
    big = "x" * (ws.PREVIEW_LIMIT + 1024)
    p = workspace / "scratch" / "big.txt"
    p.write_text(big, encoding="utf-8")
    d = logged_in.get("/files/viewer", params={"path": "scratch/big.txt"}).json()
    assert d["truncated"] is True
    assert len(d["text"]) == ws.PREVIEW_LIMIT
    assert d["size"] == p.stat().st_size


def test_viewer_missing_file_is_404(workspace, logged_in):
    r = logged_in.get("/files/viewer", params={"path": "pictures/nope.png"})
    assert r.status_code == 404   # _fail：NotFound → 404


def test_viewer_requires_login(client):
    assert client.get("/files/viewer", params={"path": "x"},
                      follow_redirects=False).status_code == 302


def test_raw_inline_allows_same_origin_framing(workspace, logged_in):
    """WI-15：PDF/HTML 走同源 iframe 框嵌——/files/raw（inline）必须放行
    同源框嵌（SAMEORIGIN + frame-ancestors 'self'），否则浏览器拒绝加载。
    其余页面保持 DENY / 'none'（点击劫持防护不全局放宽）。
    """
    (workspace / "documents" / "a.pdf").write_bytes(b"%PDF-1.4 fake")
    r = logged_in.get("/files/raw", params={"path": "documents/a.pdf"})
    assert r.status_code == 200
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in r.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" not in r.headers["Content-Security-Policy"]

    page = logged_in.get("/files")
    assert page.status_code == 200
    assert page.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]


def test_html_raw_serves_text_html(workspace, logged_in):
    """WI-19：HTML 预览源以 text/html 内联 servir，iframe 沙盒才能正确渲染。"""
    (workspace / "documents" / "page.html").write_text(
        "<!doctype html><html><body><h1>hi</h1></body></html>", encoding="utf-8")
    r = logged_in.get("/files/raw", params={"path": "documents/page.html"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    csp = r.headers["Content-Security-Policy"]
    assert "frame-ancestors 'self'" in csp
    # 沙盒开关放行后由 viewer 的 sandbox 门控执行，CSP 须允许内联脚本
    assert "script-src 'self' 'unsafe-inline'" in csp


def test_raw_html_relaxed_csp_and_html_only(workspace, logged_in):
    """WI-19B：宽松档专用源——仅 HTML，CSP 放行名单 CDN + 顶层沙盒。"""
    (workspace / "documents" / "page.html").write_text("<h1>hi</h1>", encoding="utf-8")
    (workspace / "documents" / "notes.txt").write_text("x", encoding="utf-8")
    r = logged_in.get("/files/raw-html", params={"path": "documents/page.html"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    csp = r.headers["Content-Security-Policy"]
    assert "cdn.jsdelivr.net" in csp
    assert "frame-ancestors 'self'" in csp
    assert "sandbox allow-scripts allow-forms" in csp
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    # 非 HTML 拒绝（不给宽松 CSP 当通用跳板）
    r2 = logged_in.get("/files/raw-html", params={"path": "documents/notes.txt"})
    assert r2.status_code == 400
    # 严格源不受影响（无名单、无顶层 sandbox）
    r3 = logged_in.get("/files/raw", params={"path": "documents/page.html"})
    assert "cdn.jsdelivr.net" not in r3.headers["Content-Security-Policy"]
    assert "sandbox" not in r3.headers["Content-Security-Policy"]
