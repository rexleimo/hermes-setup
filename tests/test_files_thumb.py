"""图片缩略图（0.8.33）回归：网格缩略图不再拉原图。

覆盖：/files/thumb 生成 320px JPEG、磁盘缓存键含 mtime（文件一变换新键）、
非图片/缺失/越狱路径的拒绝、模板网格用 /files/thumb 而非 /files/raw。
"""
from __future__ import annotations

import io

import pytest

from app.hermes import engineering_service as eng
from app.hermes import workspace_service as ws
from tests.conftest import csrf_of


@pytest.fixture()
def photo_workspace(client, logged_in, tmp_path):
    """已初始化且带一张 800×600 PNG 的工作区。"""
    from PIL import Image

    w = tmp_path / "ws-thumb"
    s = eng.EngSettings(workspace=str(w))
    eng.save_settings(s)
    eng.init_workspace(s)
    ws.invalidate_caches()
    Image.new("RGB", (800, 600), (200, 60, 60)).save(w / "downloads" / "pic.png")
    return w


def test_thumb_generates_small_jpeg(client, logged_in, photo_workspace):
    r = client.get("/files/thumb", params={"path": "downloads/pic.png"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/jpeg")
    assert "immutable" in r.headers.get("cache-control", "")
    from PIL import Image

    im = Image.open(io.BytesIO(r.content))
    assert max(im.size) <= ws.THUMB_MAX, "最长边必须压到 320px"


def test_thumb_cache_reused_and_invalidated(client, logged_in, photo_workspace):
    from app.core.settings import settings

    thumbs_dir = settings.data_dir / "thumbs"
    # data_dir 全测试共享：清掉此前用例的缓存再数，避免把别处的键算进来
    import shutil

    shutil.rmtree(thumbs_dir, ignore_errors=True)
    r1 = client.get("/files/thumb", params={"path": "downloads/pic.png"})
    assert r1.status_code == 200
    assert len(list(thumbs_dir.glob("*.jpg"))) == 1, "首次生成落盘一份"
    client.get("/files/thumb", params={"path": "downloads/pic.png"})
    assert len(list(thumbs_dir.glob("*.jpg"))) == 1, "第二次命中缓存不新增"

    # 内容变化（mtime/size 变）→ 换新键，旧缓存不删除但不再使用
    (photo_workspace / "downloads" / "pic.png").write_bytes(
        (photo_workspace / "downloads" / "pic.png").read_bytes() + b"x")
    r2 = client.get("/files/thumb", params={"path": "downloads/pic.png"})
    assert r2.status_code == 200
    assert len(list(thumbs_dir.glob("*.jpg"))) == 2, "文件变化后生成新键"


def test_thumb_rejects_non_image_and_missing(client, logged_in, photo_workspace):
    (photo_workspace / "downloads" / "note.txt").write_text("x", encoding="utf-8")
    assert client.get("/files/thumb", params={"path": "downloads/note.txt"}).status_code == 400
    assert client.get("/files/thumb", params={"path": "downloads/gone.png"}).status_code == 404
    assert client.get("/files/thumb", params={"path": "../../etc"}).status_code == 400


def test_thumb_requires_login(photo_workspace):
    # 不带登录 cookie 的全新客户端：thumb 不得泄漏工作区字节
    from fastapi.testclient import TestClient

    from app.main import app

    fresh = TestClient(app)
    r = fresh.get("/files/thumb", params={"path": "downloads/pic.png"},
                  follow_redirects=False)
    assert r.status_code in (301, 302, 303, 401, 403), "未登录不得读工作区字节"


def test_grid_template_uses_thumb_not_raw():
    from pathlib import Path

    tpl = Path("app/web/templates/files/_content.html").read_text(encoding="utf-8")
    assert "/files/thumb?path=" in tpl, "网格图片必须走缩略图端点"
    # 原图只允许出现在视频 preload=metadata（浏览器只拉元数据）里
    assert tpl.count("/files/raw?path=") == 1 and "preload=\"metadata\"" in tpl
