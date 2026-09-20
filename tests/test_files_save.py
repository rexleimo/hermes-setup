"""窗口文本编辑保存（WI-16）：POST /files/save 覆盖 / 另存副本。"""
from __future__ import annotations

import pytest

from app.core import appsettings
from app.hermes import engineering_service as eng
from tests.conftest import csrf_of, login


@pytest.fixture()
def workspace(logged_in, tmp_path):
    ws = tmp_path / "hermes-workspace"
    s = eng.EngSettings(workspace=str(ws))
    eng.save_settings(s)
    eng.init_workspace(s)
    return ws


def _token(c) -> str:
    return csrf_of(c)


def _save(c, token, **kw):
    kw.setdefault("_csrf", token)
    return c.post("/files/save", headers={"X-CSRF-Token": token}, data=kw)


def test_save_overwrite(workspace, logged_in):
    (workspace / "documents" / "n.txt").write_text("old", encoding="utf-8")
    r = _save(logged_in, _token(logged_in), path="documents/n.txt",
              content="new content", mode="overwrite")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "rel": "documents/n.txt", "mode": "overwrite"}
    assert (workspace / "documents" / "n.txt").read_text(encoding="utf-8") == "new content"


def test_save_copy_uniquifies(workspace, logged_in):
    (workspace / "documents" / "n.txt").write_text("old", encoding="utf-8")
    r = _save(logged_in, _token(logged_in), path="documents/n.txt",
              content="copy body", mode="copy", name="n.txt")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["mode"] == "copy"
    assert d["rel"] == "documents/n-1.txt"   # 同名自动加后缀
    assert (workspace / "documents" / "n.txt").read_text(encoding="utf-8") == "old"
    assert (workspace / "documents" / "n-1.txt").read_text(encoding="utf-8") == "copy body"


def test_save_overwrite_refuses_truncated(workspace, logged_in):
    from app.hermes import workspace_service as ws
    big = workspace / "scratch" / "big.txt"
    big.write_text("x" * (ws.PREVIEW_LIMIT + 16), encoding="utf-8")
    r = _save(logged_in, _token(logged_in), path="scratch/big.txt",
              content="would lose data", mode="overwrite")
    assert r.status_code == 400   # 预览截断 → 拒绝覆盖防丢数据


def test_save_rejects_oversize_and_bad_path(workspace, logged_in):
    from app.hermes import workspace_service as ws
    (workspace / "documents" / "n.txt").write_text("old", encoding="utf-8")
    r = _save(logged_in, _token(logged_in), path="documents/n.txt",
              content="x" * (ws.SAVE_LIMIT + 1), mode="overwrite")
    assert r.status_code == 400
    r = _save(logged_in, _token(logged_in), path="../../etc/passwd",
              content="x", mode="overwrite")
    assert r.status_code in (400, 404)


def test_save_missing_file_is_404(workspace, logged_in):
    r = _save(logged_in, _token(logged_in), path="documents/nope.txt",
              content="x", mode="overwrite")
    assert r.status_code == 404


def test_save_denied_for_non_admin(workspace, admin):
    appsettings.create_user("op16", "Op16!securePass", role="operator")
    c = admin
    login(c, "op16", "Op16!securePass")
    token = _token(c)
    r = c.post("/files/save", headers={"X-CSRF-Token": token},
               data={"path": "x", "content": "y", "_csrf": token})
    assert r.status_code == 403
