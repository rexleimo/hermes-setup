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
