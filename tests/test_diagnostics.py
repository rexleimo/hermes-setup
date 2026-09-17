"""小白可用性套件：运行体检页、帮助页、DB 快照恢复、数据目录判定、预设注册链接。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core import backup, db
from app.core.appsettings import create_user
from app.core.settings import resolve_data_dir
from tests.conftest import login

# ---------------------------------------------------------------------------
# 数据目录三级判定
# ---------------------------------------------------------------------------

def test_resolve_data_dir_precedence(tmp_path):
    legacy = tmp_path / "repo" / "data"
    legacy.mkdir(parents=True)
    home = tmp_path / "home"

    # 1) 环境变量永远优先
    assert resolve_data_dir("/custom/data", legacy, home) == Path("/custom/data")
    # 2) 旧部署（项目内 data/ 已有 console.db）→ 原地沿用
    (legacy / "console.db").touch()
    assert resolve_data_dir("", legacy, home) == legacy
    # 3) 全新安装 → 主目录（升级替换项目目录不丢数据）
    (legacy / "console.db").unlink()
    assert resolve_data_dir("", legacy, home) == home


# ---------------------------------------------------------------------------
# DB 快照恢复
# ---------------------------------------------------------------------------

@pytest.fixture()
def _backups_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "backups_dir", lambda: tmp_path)
    return tmp_path


def test_restore_backup_roundtrip(_backups_dir):
    create_user("restore-marker", "Password123", role="operator")
    snap = backup.create_backup()
    assert snap is not None

    db.execute("DELETE FROM users WHERE username='restore-marker'")
    assert db.query_one(
        "SELECT * FROM users WHERE username='restore-marker'") is None

    safety = backup.restore_backup(snap.name)
    assert safety is not None and safety.exists()
    assert db.query_one(
        "SELECT * FROM users WHERE username='restore-marker'") is not None


def test_restore_backup_rejects_bad_names(_backups_dir):
    with pytest.raises(backup.BackupError):
        backup.restore_backup("../evil.db")          # 路径穿越
    with pytest.raises(backup.BackupError):
        backup.restore_backup("console-20260101-000000.db")  # 不存在
    with pytest.raises(backup.BackupError):
        backup.restore_backup("random.db")           # 命名不合法


def test_create_backup_same_second_no_clobber(_backups_dir):
    """同秒连续备份（如恢复前的安全备份）必须加序号，不能覆盖既有快照。"""
    a = backup.create_backup()
    b = backup.create_backup()
    assert a is not None and b is not None
    assert a != b
    assert a.exists() and b.exists()
    # 带序号的文件名同样可作为恢复源
    assert backup.restore_backup(b.name) is not None


# ---------------------------------------------------------------------------
# 体检页检查项构造
# ---------------------------------------------------------------------------

def test_build_checks_shape(hermes_home):
    from app.web.routers.diagnose import build_checks

    checks = build_checks(hermes_home)
    assert checks, "至少应产出一条检查项"
    for c in checks:
        assert set(c) == {"label", "state", "detail", "href", "action_label"}
        assert c["state"] in ("ok", "warn", "fail")
        # 失败/待办项必须给出去处或说明，不允许只报错不指路
        if c["state"] != "ok":
            assert c["detail"]


def test_presets_have_signup_urls():
    from app.hermes.schema import PRESETS

    missing = [p.id for p in PRESETS.values()
               if not p.signup and p.id != "lmstudio"]  # lmstudio 本地运行无需 Key
    assert missing == []


# ---------------------------------------------------------------------------
# Web 页面
# ---------------------------------------------------------------------------

def test_diagnose_and_help_require_login(client):
    for path in ("/diagnose", "/diagnose/network", "/help"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 302, path
        assert "/login" in resp.headers["location"]


def test_diagnose_page(admin):
    login(admin, "admin", "Sup3rSecure!x")
    resp = admin.get("/diagnose")
    assert resp.status_code == 200
    assert "运行体检" in resp.text
    assert "网络连通" in resp.text
    assert "/diagnose/network" in resp.text


def test_diagnose_network_fragment(admin, monkeypatch):
    from app.web.routers import diagnose as diag

    login(admin, "admin", "Sup3rSecure!x")

    monkeypatch.setattr(diag, "_probe", lambda url, timeout=5.0: True)
    resp = admin.get("/diagnose/network")
    assert resp.status_code == 200
    assert "连通" in resp.text

    monkeypatch.setattr(diag, "_probe", lambda url, timeout=5.0: False)
    resp = admin.get("/diagnose/network")
    assert "不通" in resp.text
    assert "tuna.tsinghua.edu.cn" in resp.text  # 失败时给出镜像解法


def test_help_page(admin):
    login(admin, "admin", "Sup3rSecure!x")
    resp = admin.get("/help")
    assert resp.status_code == 200
    for needle in ("它是怎么工作的", "概念速查", "忘记管理员密码", "如何升级控制台",
                   "如何彻底卸载"):
        assert needle in resp.text


def test_backup_restore_flow(admin, _backups_dir):
    login(admin, "admin", "Sup3rSecure!x")

    # 至少一份快照 → 设置页出现恢复按钮
    assert backup.create_backup() is not None
    page = admin.get("/settings")
    assert page.status_code == 200
    assert "/settings/backup/restore" in page.text
    assert "控制台数据目录" in page.text

    import re
    token = re.search(r'name="_csrf" value="([^"]*)"', page.text).group(1)
    snap_name = backup.list_backups()[0].name
    resp = admin.post("/settings/backup/restore",
                      data={"name": snap_name, "_csrf": token},
                      follow_redirects=False)
    assert resp.status_code == 303

    rows = db.query("SELECT * FROM audit_log WHERE action='db_restore'")
    assert rows and "snapshot=" in rows[-1]["detail"]
    # 当前管理员会话应在恢复后保留（不被自己踢下线）
    assert admin.get("/settings").status_code == 200


def test_backup_restore_rejects_bad_name(admin, _backups_dir):
    import re
    login(admin, "admin", "Sup3rSecure!x")
    page = admin.get("/settings")
    token = re.search(r'name="_csrf" value="([^"]*)"', page.text).group(1)
    resp = admin.post("/settings/backup/restore",
                      data={"name": "../evil.db", "_csrf": token},
                      follow_redirects=False)
    assert resp.status_code == 303  # 拒绝但要回到设置页并提示
    rows = db.query("SELECT * FROM audit_log WHERE action='db_restore' "
                    "AND outcome='failed'")
    assert rows


def test_install_preflight_blocks_offline(admin, monkeypatch):
    """网络预检不通过时，安装任务直接以大白话拒绝，不提交必败任务。"""
    import re

    from app.hermes import installer

    login(admin, "admin", "Sup3rSecure!x")
    monkeypatch.setattr(installer, "network_reachable", lambda *a, **k: False)
    before = db.query_one("SELECT COUNT(*) AS n FROM job_runs")["n"]
    page = admin.get("/service")
    token = re.search(r'name="_csrf" value="([^"]*)"', page.text).group(1)
    resp = admin.post("/service/install", data={"_csrf": token},
                      follow_redirects=False)
    assert resp.status_code == 400
    assert "GitHub" in resp.text
    after = db.query_one("SELECT COUNT(*) AS n FROM job_runs")["n"]
    assert before == after  # 没有提交任务
