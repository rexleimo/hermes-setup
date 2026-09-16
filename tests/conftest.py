"""pytest 公共夹具：隔离的数据目录、初始化后的管理员、已登录客户端。"""
from __future__ import annotations

import os
import tempfile

# 必须在导入 app 之前设置环境（settings 是导入期实例化的单例）
_TMP = tempfile.mkdtemp(prefix="hermes-console-test-")
os.environ["HERMES_CONSOLE_DATA"] = _TMP
os.environ["HERMES_CONSOLE_SECRET"] = "test-secret-key-for-pytest-only-0123456789"
os.environ["HERMES_CONSOLE_SESSION_TTL"] = "60"
os.environ["HERMES_CONSOLE_IDLE_TTL"] = "30"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import db, sessions as session_store  # noqa: E402
from app.core.appsettings import create_user, set_setting  # noqa: E402
from app.hermes.paths import HermesPaths  # noqa: E402
from main_test_helpers import fresh_hermes_home  # noqa: E402

ADMIN_USER = "admin"
ADMIN_PASS = "Sup3rSecure!x"


@pytest.fixture(autouse=True)
def clean_db():
    """每个测试前：建表并清空业务数据，保证用例互相独立。"""
    db.init_db()
    for table in ("users", "sessions", "audit_log", "login_attempts",
                  "app_settings", "provider_meta", "provider_models",
                  "fallback_chain", "job_runs", "mcp_meta"):
        db.execute(f"DELETE FROM {table}")
    yield


@pytest.fixture()
def hermes_home(tmp_path):
    """把 Hermes 家目录指向临时路径（paths.detect 的模块级 override）。"""
    home = tmp_path / "hermes-home"
    home.mkdir(parents=True)
    paths = HermesPaths(home=home, bin=None)
    from app.hermes import paths as paths_module

    paths_module.set_override(paths)
    try:
        fresh_hermes_home(home)
        yield paths
    finally:
        paths_module.set_override(None)


@pytest.fixture()
def client(hermes_home):
    from app.main import app

    db.init_db()
    with TestClient(app, base_url="http://testserver") as c:
        yield c


@pytest.fixture()
def admin(client):
    """已创建管理员的数据库。"""
    create_user(ADMIN_USER, ADMIN_PASS, role="admin")
    return client


@pytest.fixture()
def logged_in(admin):
    """已登录（带 cookie）的客户端与 csrf token。"""
    return login(admin, ADMIN_USER, ADMIN_PASS)


def csrf_of(client: TestClient) -> str:
    page = client.get("/login").text
    marker = 'name="_csrf" value="'
    start = page.find(marker)
    end = page.find('"', start + len(marker))
    return page[start + len(marker):end]


def login(client: TestClient, username: str, password: str) -> TestClient:
    token = csrf_of(client)
    resp = client.post("/login", data={"username": username, "password": password,
                                       "_csrf": token}, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return client


__all__ = ["session_store", "login", "csrf_of", "ADMIN_USER", "ADMIN_PASS"]
