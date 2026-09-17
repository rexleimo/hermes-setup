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
    lines = ["Traceback (most recent call last):", "OSError: [WinError 10106] boom"]
    monkeypatch.setattr(onboarding.installer, "job_log", lambda jid, tail=60: lines)
    st = onboarding.qr_state()
    assert st["phase"] == "error"
    # 小白看不到日志文件：驱动崩溃的真实输出必须随面板状态透出
    assert "WinError 10106" in "\n".join(st["log_tail"])


def test_qr_state_error_event_keeps_log_tail(monkeypatch):
    monkeypatch.setattr(onboarding, "_latest_qr_job",
                        lambda: {"id": 9, "status": "failed"})
    lines = _events({"type": "error", "msg": "获取二维码失败"}) + ["some noise"]
    monkeypatch.setattr(onboarding.installer, "job_log", lambda jid, tail=60: lines)
    st = onboarding.qr_state()
    assert st["phase"] == "error" and st["error"] == "获取二维码失败"
    assert st["log_tail"] == ["some noise"]  # EVENT 机器行不展示给用户


def test_job_worker_inherits_full_environment(monkeypatch):
    """回归：任务子进程必须继承完整环境（Windows 下硬编码 POSIX PATH 会导致
    子进程 python Winsock 初始化失败，二维码任务直接崩溃）。"""
    import io
    import os
    from app.hermes import installer

    captured = {}

    class FakeProc:
        returncode = 0
        stdout = io.BytesIO()

        def wait(self, timeout=None):
            return 0

    def fake_popen(cmd, **kw):
        captured["env"] = kw["env"]
        return FakeProc()

    class FakeThread:
        def __init__(self, target=None, *a, **kw):
            self._target = target
            self._args = a
            self._kwargs = kw

        def start(self):
            extra = self._kwargs.get("args") or ()
            self._target(*self._args, *extra)

        def join(self, timeout=None):
            return None

    monkeypatch.setattr(installer.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(installer.threading, "Thread", FakeThread)
    installer.submit("test_env", "true")
    assert captured["env"]["PATH"] == os.environ["PATH"]


def test_channel_pages_render_guide_with_links(logged_in):
    """表单类渠道页必须内嵌带可点击直达链接的分步引导（小白不读文档）。"""
    r = logged_in.get("/channels/telegram")
    assert r.status_code == 200
    assert "接入引导" in r.text
    assert 'href="https://t.me/BotFather"' in r.text
    r = logged_in.get("/channels/feishu")
    assert 'href="https://open.feishu.cn/app"' in r.text
    # 所有非 weixin 渠道都要有引导（weixin 走接入助手）
    from app.hermes.schema import PLATFORMS
    for name, d in PLATFORMS.items():
        if name == "weixin":
            continue
        assert d.guide_steps, f"{name} 缺少接入引导步骤"
        assert any(s.url for s in d.guide_steps), f"{name} 引导缺可点击链接"


def test_apply_weixin_account_writes_config(hermes_home):
    changes = onboarding.apply_weixin_account("abc123@im.bot")
    assert changes, "首次回填应产生变更"
    view = channels_service.get_channel("weixin")
    assert view.extra.get("account_id") == "abc123@im.bot"
    assert view.enabled
    # 重复回填不报错（写入同值），配置保持稳定
    onboarding.apply_weixin_account("abc123@im.bot")
    assert channels_service.get_channel("weixin").extra.get("account_id") == "abc123@im.bot"


def test_apply_weixin_account_fixes_open_policy_guard(hermes_home):
    """扫码回填必须顺手消除「dm_policy=open 无白名单 → Gateway 拒启」的地雷：
    号主写入 WEIXIN_ALLOWED_USERS，dm_policy 收敛为 allowlist，且幂等。"""
    from app.hermes.config_store import EnvStore
    changes = onboarding.apply_weixin_account(
        "abc123@im.bot", "owner@im.wechat")
    assert changes
    view = channels_service.get_channel("weixin")
    assert str(view.config_values.get("dm_policy")) == "allowlist"
    assert EnvStore.load().get("WEIXIN_ALLOWED_USERS") == "owner@im.wechat"
    # 小白默认：自动设 home_channel（消除 📬 催促）+ busy 排队（消除 ↪ Redirected 插队）
    from app.hermes.config_store import load_config
    cfg = load_config()
    home = cfg["platforms"]["weixin"].get("home_channel")
    assert home and home["chat_id"] == "owner@im.wechat"
    assert str(cfg["display"]["busy_text_mode"]) == "queue"
    # 再次回填同值 → 不重复变更（幂等）
    assert onboarding.apply_weixin_account("abc123@im.bot", "owner@im.wechat") == []
    # 已有白名单时追加而非覆盖
    onboarding.apply_weixin_account("abc123@im.bot", "second@im.wechat")
    assert EnvStore.load().get("WEIXIN_ALLOWED_USERS") == "owner@im.wechat,second@im.wechat"


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
