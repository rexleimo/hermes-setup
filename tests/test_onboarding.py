"""渠道接入助手：事件解析、扫码成功自动回填、权限门槛。"""
from __future__ import annotations

import pytest

from app.core import appsettings
from app.hermes import channels_service, onboarding
from tests.conftest import ADMIN_PASS, csrf_of, login


def _events(*objs):
    return ["EVENT " + __import__("json").dumps(o, ensure_ascii=False) for o in objs]


def test_qr_state_parses_event_log(monkeypatch):
    monkeypatch.setattr(onboarding, "_latest_qr_job",
                        lambda: {"id": 7, "status": "running"})
    lines = _events({"type": "qr", "url": "https://lite.dz.com/a"},
                    {"type": "status", "status": "scaned"})
    monkeypatch.setattr(onboarding.installer, "job_log", lambda jid, tail=60: lines)
    st = onboarding.qr_state()
    assert st["phase"] == "scaned" and st["qr_url"] == "https://lite.dz.com/a"

    lines += _events({"type": "qr", "url": "https://lite.dz.com/b"},
                     {"type": "success", "account_id": "a5ac@im.bot"})
    st = onboarding.qr_state()
    assert st["phase"] == "confirmed" and st["account_id"] == "a5ac@im.bot"
    assert st["qr_url"] == "https://lite.dz.com/b"  # 最新二维码覆盖旧的


def test_qr_state_error_when_job_died_without_events(monkeypatch):
    monkeypatch.setattr(onboarding, "_latest_qr_job",
                        lambda: {"id": 8, "status": "failed"})
    monkeypatch.setattr(onboarding.installer, "job_log", lambda jid, tail=60: [])
    assert onboarding.qr_state()["phase"] == "error"


def test_apply_weixin_account_writes_config(hermes_home):
    changes = onboarding.apply_weixin_account("abc123@im.bot")
    assert changes, "首次回填应产生变更"
    view = channels_service.get_channel("weixin")
    assert view.extra.get("account_id") == "abc123@im.bot"
    assert view.enabled
    # 重复回填不报错（写入同值），配置保持稳定
    onboarding.apply_weixin_account("abc123@im.bot")
    assert channels_service.get_channel("weixin").extra.get("account_id") == "abc123@im.bot"


def _confirmed_panel(monkeypatch):
    monkeypatch.setattr(onboarding, "qr_state", lambda: {
        "phase": "confirmed", "job_id": 1,
        "qr_url": "https://lite.dz.com/x", "account_id": "zz9@im.bot",
        "error": None, "job_status": "ok"})
    monkeypatch.setattr(onboarding, "deps_status",
                        lambda **kw: {"checked": True, "ok": True, "missing": []})


def test_panel_backfills_on_poll(logged_in, hermes_home, monkeypatch):
    _confirmed_panel(monkeypatch)
    r = logged_in.get("/channels/weixin/onboard")
    assert r.status_code == 200
    assert "微信已连接" in r.text
    view = channels_service.get_channel("weixin")
    assert view.extra.get("account_id") == "zz9@im.bot"


def test_onboard_non_weixin_is_404(logged_in):
    assert logged_in.get("/channels/telegram/onboard").status_code == 404


def test_qr_start_and_deps_install_require_admin(logged_in, hermes_home):
    appsettings.create_user("ob9", "Ob9!securePass", role="operator")
    client = login(logged_in, "ob9", "Ob9!securePass")
    token = csrf_of(client)
    r = client.post("/channels/weixin/qr-start", data={"_csrf": token})
    assert r.status_code == 403, r.text
    r = client.post("/channels/weixin/deps-install", data={"_csrf": token})
    assert r.status_code == 403, r.text


def test_onboard_panel_requires_login(client):
    r = client.get("/channels/weixin/onboard", follow_redirects=False)
    assert r.status_code in (302, 303)  # 未登录跳转登录页
