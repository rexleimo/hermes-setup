"""文件工作台（W3）：jail 强制、登录门槛、上传/归档/打包行为。"""
from __future__ import annotations


import pytest

from app.core import appsettings, db
from app.hermes import engineering_service as eng
from tests.conftest import ADMIN_PASS, csrf_of, login


@pytest.fixture()
def workspace(logged_in, tmp_path):
    """已登录 admin + workspace 指向 tmp 专属目录并已初始化目录树。"""
    ws = tmp_path / "hermes-workspace"
    s = eng.EngSettings(workspace=str(ws))
    eng.save_settings(s)
    eng.init_workspace(s)
    return ws


def _token(c) -> str:
    return csrf_of(c)


def test_files_requires_login(client):
    assert client.get("/files", follow_redirects=False).status_code == 302


def test_jail_blocks_traversal(workspace, logged_in):
    for bad in ("../../etc/passwd", "../../../Windows/win.ini", "/etc/passwd"):
        r = logged_in.get("/files/raw", params={"path": bad, "dl": 1})
        assert r.status_code in (400, 404), (bad, r.status_code)


def test_root_validation_rejects_shallow(workspace):
    from app.hermes import workspace_service as ws
    with pytest.raises(ws.WorkspaceError):
        ws.validate_root("~")
    with pytest.raises(ws.WorkspaceError):
        ws.validate_root("/")


def test_browse_and_preview(workspace, logged_in):
    page = logged_in.get("/files")
    assert page.status_code == 200
    assert "projects" in page.text
    frag = logged_in.get("/files/list", params={"path": "projects"})
    assert frag.status_code == 200
    assert "example-project" in frag.text
    pv = logged_in.get("/files/preview", params={"path": "README.md"})
    assert pv.status_code == 200
    assert pv.text.strip() != ""


def test_raw_no_cache_revalidation(workspace, logged_in):
    # 工作区文件可变（Agent 随时产出）：no-cache 强制按 ETag 再验证，
    # 否则 viewer/缩略图会缓存到更新前的旧内容（E2E 实锤过坏图问题）
    r = logged_in.get("/files/raw", params={"path": "README.md"})
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"
    assert r.headers.get("etag"), "FileResponse 应提供 ETag 供再验证"


def test_upload_no_overwrite_and_archive(workspace, logged_in):
    c = logged_in
    token = _token(c)
    r = c.post("/files/upload", headers={"X-CSRF-Token": token},
               files={"file": ("notes.txt", b"hello from console", "text/plain")},
               follow_redirects=False)
    assert r.status_code in (200, 303), r.text
    up = workspace / "downloads" / "notes.txt"
    assert up.exists() and up.read_bytes() == b"hello from console"
    c.post("/files/upload", headers={"X-CSRF-Token": token},
           files={"file": ("notes.txt", b"v2", "text/plain")},
           follow_redirects=False)
    assert list((workspace / "downloads").glob("notes-*.txt"))   # 不覆盖
    r = c.post("/files/move", headers={"X-CSRF-Token": token},
               data={"path": "downloads/notes.txt", "_csrf": token})
    assert r.status_code == 200
    assert not up.exists()
    assert (workspace / "archive" / "notes.txt").exists()


def test_move_renders_current_view_with_side_oob(workspace, logged_in):
    """归档（带 here=当前视图）：原地重渲染 + 侧栏 OOB，不整页刷新。"""
    c = logged_in
    token = _token(c)
    for name in ("a.txt", "b.txt"):
        c.post("/files/upload", headers={"X-CSRF-Token": token},
               files={"file": (name, name.encode(), "text/plain")},
               follow_redirects=False)
    r = c.post("/files/move", headers={"X-CSRF-Token": token},
               data={"path": "downloads/a.txt",
                     "here": "path=downloads&sort=name&view=grid",
                     "_csrf": token})
    assert r.status_code == 200
    body = r.text
    assert 'id="osfm-items"' in body, "主响应体应是当前视图网格"
    assert 'hx-swap-oob="true"' in body and 'id="osfm-side"' in body, "侧栏计数应 OOB 换入"
    assert "b.txt" in body and "a.txt" not in body, "归档项应从当前视图消失"
    assert "console:toast" in r.headers.get("HX-Trigger", ""), "应带 toast 事件"
    assert (workspace / "archive" / "a.txt").exists()


def test_upload_htmx_returns_nav_trigger(workspace, logged_in):
    """HTMX 上传：200 + HX-Trigger（console:toast + osfm:nav），前端 boosted 导航不白屏。

    不用 HX-Redirect：HTMX 1.9 的 HX-Redirect 是整页跳转（白屏）。
    """
    import json as _json
    c = logged_in
    token = _token(c)
    r = c.post("/files/upload",
               headers={"X-CSRF-Token": token, "HX-Request": "true"},
               files={"file": ("u.txt", b"U", "text/plain")},
               follow_redirects=False)
    assert r.status_code == 200
    assert r.headers.get("HX-Redirect") is None
    trig = _json.loads(r.headers["HX-Trigger"])
    assert trig["console:toast"]["message"].endswith("downloads/u.txt")
    assert trig["osfm:nav"]["to"] == "/files?path=downloads"
    assert (workspace / "downloads" / "u.txt").exists()


def test_upload_non_htmx_redirects(workspace, logged_in):
    """非 HTMX 上传：303 → 目标目录。"""
    c = logged_in
    token = _token(c)
    r = c.post("/files/upload", headers={"X-CSRF-Token": token},
               files={"file": ("v.txt", b"V", "text/plain")},
               follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/files?path=downloads")


def test_upload_denied_for_non_admin(workspace, admin):
    appsettings.create_user("op9", "Op9!securePass", role="operator")
    c = admin
    login(c, "op9", "Op9!securePass")
    token = _token(c)
    r = c.post("/files/upload", headers={"X-CSRF-Token": token},
               files={"file": ("a.txt", b"x", "text/plain")})
    assert r.status_code == 403


def test_zip(workspace, logged_in):
    r = logged_in.get("/files/zip", params={"path": "projects/example-project"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    assert len(r.content) > 100


def test_denied_is_audited(workspace, logged_in):
    logged_in.get("/files/raw", params={"path": "../../x", "dl": 1})
    rows = db.query("SELECT action, outcome FROM audit_log WHERE action='files_denied'")
    assert rows, "越狱尝试必须落审计"


# ---------------------------------------------------------------------------
# OS 风格改造（v0.5.0）：类型体系与智能集合
# ---------------------------------------------------------------------------

def test_smart_collections_and_os_layout(logged_in, workspace):
    import io
    # 初始化后存在标准类型目录
    for d in ("documents", "pictures", "videos"):
        assert (workspace / d).is_dir(), d
    # 放一张"图片"和一段"视频"在深层目录，验证全工作区聚合
    deep = workspace / "projects" / "example-project" / "docs"
    (deep / "shot.png").write_bytes(b"\x89PNG fake")
    (deep / "clip.mp4").write_bytes(b"fakevideo")
    page = logged_in.get("/files?cat=images")
    assert page.status_code == 200 and "shot.png" in page.text
    assert "clip.mp4" not in page.text  # 集合按类型过滤
    frag = logged_in.get("/files/list", params={"cat": "videos"})
    assert frag.status_code == 200 and "clip.mp4" in frag.text
    # 智能集合是虚拟视图：文件没有被移动
    assert (deep / "shot.png").exists()
    # 最近使用视图
    rec = logged_in.get("/files?cat=recent")
    assert rec.status_code == 200 and "shot.png" in rec.text
    # 未知集合 → 400
    assert logged_in.get("/files", params={"cat": "badgers"}).status_code == 400


def test_entry_sorting_and_dir_counts(logged_in, workspace):
    (workspace / "scratch" / "a_big.txt").write_bytes(b"x" * 2048)
    (workspace / "scratch" / "z_small.txt").write_text("s", encoding="utf-8")
    by_size = logged_in.get("/files/list", params={"path": "scratch", "sort": "size"}).text
    assert by_size.index("a_big.txt") < by_size.index("z_small.txt")
    page = logged_in.get("/files?path=projects").text
    assert "example-project" in page  # 目录卡片（含数量徽标）
