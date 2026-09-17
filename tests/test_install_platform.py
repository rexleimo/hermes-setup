"""Windows 一键安装走原生通道 + 全新机默认家目录/检测回归。

背景：控制台曾在所有平台跑 `curl | bash` 装 install.sh（Linux/macOS 专用脚本），
Windows 上 bash 解析到 WSL 存根，装不出原生 hermes；且默认家目录 ~/.hermes
与官方 PS 安装器落点（%LOCALAPPDATA%\\hermes）错位。以下用例锁死这两处。
"""
from __future__ import annotations

import re
import sys
from urllib.parse import urlparse

import pytest

from app.hermes import installer
from app.hermes import paths as paths_mod
from tests.conftest import login


def test_install_cmd_platform_contract():
    """安装命令必须与当前平台原生通道一致，且后台无 TTY 必须跳过交互。"""
    if sys.platform == "win32":
        assert "powershell" in installer.INSTALL_CMD.lower()
        assert "install.ps1" in installer.INSTALL_CMD
        assert "-SkipSetup" in installer.INSTALL_CMD
        assert "-NonInteractive" in installer.INSTALL_CMD
        assert "镜像" in installer.INSTALL_METHOD_LABEL  # 默认国内镜像源（大陆优先）
    else:
        assert "install.sh" in installer.INSTALL_CMD
        assert "bash" in installer.INSTALL_CMD
        assert "--skip-setup" in installer.INSTALL_CMD


def test_install_variant_both_sources():
    """国内镜像源与官方源都要齐备（镜像大陆优先，官方兜底）。"""
    cn = installer.install_variant("cn")
    official = installer.install_variant("official")
    for v in (cn, official):
        assert set(v) == {"source", "label", "cmd", "preflight"}
        assert v["source"] in ("cn", "official")
    assert "res1.hermesagent.org.cn" in cn["cmd"]
    if sys.platform == "win32":
        assert "install.ps1" in cn["cmd"] and "install.ps1" in official["cmd"]
        assert "nousresearch.com" in official["preflight"]
    else:
        assert "install.sh" in cn["cmd"] and "install.sh" in official["cmd"]
        assert "--skip-browser" in cn["cmd"]  # 浏览器组件走装后接力
        assert "githubusercontent.com" in official["cmd"]


def test_choose_install_prefers_cn_and_falls_back(monkeypatch):
    """自动选源：国内镜像优先；镜像不可达回退官方；都不达返回 None。"""
    monkeypatch.setattr(installer, "network_reachable",
                        lambda url, **kw: "res1.hermesagent.org.cn" in url)
    assert installer.choose_install()["source"] == "cn"

    monkeypatch.setattr(installer, "network_reachable",
                        lambda url, **kw: "res1.hermesagent.org.cn" not in url)
    assert installer.choose_install()["source"] == "official"

    monkeypatch.setattr(installer, "network_reachable", lambda *a, **k: False)
    assert installer.choose_install() is None


def test_forced_install_source_env(monkeypatch):
    """显式指定安装源时只认该源（便于固定排查），且不做跨源回退。"""
    monkeypatch.setenv("HERMES_CONSOLE_INSTALL_SOURCE", "official")
    assert installer.preferred_install()["source"] == "official"
    monkeypatch.setattr(installer, "network_reachable",
                        lambda url, **kw: "res1.hermesagent.org.cn" in url)
    assert installer.choose_install() is None  # 强制 official 时镜像可达也不算数
    monkeypatch.setattr(installer, "network_reachable", lambda *a, **k: True)
    assert installer.choose_install()["source"] == "official"


def test_install_preflight_matches_channel():
    """安装前网络预检必须与实际安装源同域，否则"预检通过、执行必败"。"""
    assert urlparse(installer.INSTALL_PREFLIGHT_URL).netloc in installer.INSTALL_CMD


def test_which_hermes_windows_localappdata(tmp_path, monkeypatch):
    """PATH 尚未刷新时，用 %LOCALAPPDATA% 绝对路径兜底找到刚装好的 hermes。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    exe = tmp_path / "hermes" / "bin" / "hermes.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr("shutil.which", lambda *a, **k: None)
    assert paths_mod.which_hermes() == str(exe)


def test_default_home_matches_official_installer(tmp_path, monkeypatch):
    """全新机无显式配置时，默认家目录必须等于官方安装器落点。"""
    monkeypatch.setattr(paths_mod.appsettings, "get_setting", lambda key: "")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    assert paths_mod.detect().home == (tmp_path / "hermes").resolve()
    monkeypatch.setattr(sys, "platform", "linux")
    assert paths_mod.detect().home == (tmp_path / ".hermes").resolve()


def test_network_probes_follow_platform(monkeypatch):
    from app.web.routers import diagnose as diag

    monkeypatch.setattr(sys, "platform", "win32")
    urls = [u for _, u, _ in diag.network_probes()]
    assert any("install.ps1" in u for u in urls)

    monkeypatch.setattr(sys, "platform", "linux")
    urls = [u for _, u, _ in diag.network_probes()]
    assert any("raw.githubusercontent.com" in u for u in urls)


def test_build_checks_has_git_row(hermes_home):
    """一键安装需要 Git 下拉仓库：体检必须覆盖这一项。"""
    from app.web.routers.diagnose import build_checks

    labels = [c["label"] for c in build_checks(hermes_home)]
    assert any("Git" in label for label in labels)


def test_service_page_shows_platform_install_method(admin):
    """服务页展示平台对应的安装方式与可复制命令（注意 HTML 转义）。"""
    login(admin, "admin", "Sup3rSecure!x")
    resp = admin.get("/service")
    assert resp.status_code == 200
    assert installer.INSTALL_METHOD_LABEL in resp.text
    needle = "install.ps1" if sys.platform == "win32" else "install.sh"
    assert needle in resp.text


def test_install_update_jobs_use_jobs_dir_cwd(admin, monkeypatch):
    """安装/更新任务的工作目录必须是 jobs_dir，不能污染仓库目录。"""
    from app.core.settings import settings as console_settings

    calls = {}

    def fake_submit(kind, command, *, shell=True, cwd=None):
        calls[kind] = cwd
        return 99

    monkeypatch.setattr(installer, "submit", fake_submit)
    monkeypatch.setattr(installer, "network_reachable", lambda *a, **k: True)
    login(admin, "admin", "Sup3rSecure!x")
    page = admin.get("/service").text
    token = re.search(r'name="_csrf" value="([^"]*)"', page).group(1)
    assert admin.post("/service/install", data={"_csrf": token},
                      follow_redirects=False).status_code == 200
    assert admin.post("/service/update", data={"_csrf": token},
                      follow_redirects=False).status_code == 200
    assert calls["install"] == str(console_settings.jobs_dir)
    assert calls["update"] == str(console_settings.jobs_dir)


def test_network_reachable_head_falls_back_to_get(monkeypatch):
    """部分网络能 GET 不能 HEAD：预检必须回退再探一次，避免误报不通。"""
    import urllib.request

    calls = []

    class FakeResp:
        status = 200

        def read(self, n=-1):
            return b"x"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls.append(req.get_method())
        if req.get_method() == "HEAD":
            raise OSError("head blocked")
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert installer.network_reachable("https://example.test/x") is True
    assert calls == ["HEAD", "GET"]


def test_install_preflight_reject_offers_force(admin, monkeypatch):
    """预检拦截时必须给出「仍要执行」口子，不能只报错不给路。"""
    monkeypatch.setattr(installer, "network_reachable", lambda *a, **k: False)
    login(admin, "admin", "Sup3rSecure!x")
    page = admin.get("/service").text
    token = re.search(r'name="_csrf" value="([^"]*)"', page).group(1)
    resp = admin.post("/service/install", data={"_csrf": token},
                      follow_redirects=False)
    assert resp.status_code == 400
    assert "仍要执行" in resp.text
    assert 'name="force"' in resp.text


def test_install_force_bypasses_preflight(admin, monkeypatch):
    """force=1 绕过预检直接提交任务（决策权留给用户）。"""
    calls = {}

    def fake_submit(kind, command, *, shell=True, cwd=None):
        calls["kind"] = kind
        return 42

    monkeypatch.setattr(installer, "network_reachable", lambda *a, **k: False)
    monkeypatch.setattr(installer, "submit", fake_submit)
    login(admin, "admin", "Sup3rSecure!x")
    page = admin.get("/service").text
    token = re.search(r'name="_csrf" value="([^"]*)"', page).group(1)
    resp = admin.post("/service/install",
                      data={"_csrf": token, "force": "1"},
                      follow_redirects=False)
    assert resp.status_code == 200
    assert calls.get("kind") == "install"


def test_install_command_defers_browser_to_chain(admin, monkeypatch):
    """主安装跳过官方浏览器段（POSIX），浏览器组件由装后自动接力任务负责。"""
    calls = {}

    def fake_submit(kind, command, *, shell=True, cwd=None):
        calls["command"] = command
        return 6

    monkeypatch.setattr(installer, "submit", fake_submit)
    monkeypatch.setattr(installer, "network_reachable", lambda *a, **k: True)
    login(admin, "admin", "Sup3rSecure!x")
    page = admin.get("/service").text
    token = re.search(r'name="_csrf" value="([^"]*)"', page).group(1)
    resp = admin.post("/service/install", data={"_csrf": token},
                      follow_redirects=False)
    assert resp.status_code == 200
    if sys.platform == "win32":
        assert "--skip-browser" not in calls["command"]
    else:
        assert calls["command"].endswith("--skip-browser")


def test_install_chain_after_browser_job(monkeypatch):
    """主安装成功 → 自动接力浏览器组件任务；失败/已装/Windows 都不接力。"""
    calls = []

    def fake_submit(kind, command, *, shell=True, cwd=None):
        calls.append((kind, command))
        return 9

    monkeypatch.setattr(installer, "browser_installed", lambda: False)
    monkeypatch.setattr(installer, "browser_install_job",
                        lambda: 'bash "browser_install.sh" "repo"')
    monkeypatch.setattr(installer, "submit", fake_submit)

    installer._chain_after("install", True)
    if sys.platform == "win32":
        assert calls == []          # Windows：官方安装器自带 best-effort 浏览器段
        return
    assert calls == [("browser_install", 'bash "browser_install.sh" "repo"')]

    calls.clear()
    installer._chain_after("install", False)   # 主安装失败 → 不接力
    assert calls == []

    calls.clear()
    monkeypatch.setattr(installer, "browser_installed", lambda: True)
    installer._chain_after("install", True)    # 引擎已装 → 不重复接力
    assert calls == []


def test_cancel_unknown_job():
    assert installer.cancel(99999) == "任务不存在"


def test_service_page_keeps_last_job_panel(admin):
    """最近一次任务跑完也必须常驻可看（"操作完到处找不到日志"= 不可接受）。"""
    from app.core import db
    from app.core.settings import settings

    db.execute("INSERT INTO job_runs (kind, command, log_path, status, exit_code) "
               "VALUES (?,?,?,?,?)",
               ("gateway_start", "cmd", "jobs/job-x.log", "ok", 0))
    job_id = db.query_one("SELECT id FROM job_runs ORDER BY id DESC LIMIT 1")["id"]
    db.execute("UPDATE job_runs SET log_path = ? WHERE id = ?",
               (f"jobs/job-{job_id}.log", job_id))
    (settings.jobs_dir / f"job-{job_id}.log").write_text(
        "gateway started ok\n", encoding="utf-8")
    login(admin, "admin", "Sup3rSecure!x")
    page = admin.get("/service").text
    assert "gateway started ok" in page          # 输出常驻在面板里
    assert "启动 Gateway" in page                # 历史表中文标签
    assert f"job-{job_id}.log" in page           # 日志文件路径直接给出


def test_gateway_action_submits_background_job(admin, monkeypatch):
    """网关启停必须是后台任务（有流水可看），不再是同步阻塞调用。"""
    calls = {}

    def fake_submit(kind, command, *, shell=True, cwd=None):
        calls["kind"] = kind
        calls["command"] = command
        return 11

    monkeypatch.setattr(installer, "submit", fake_submit)
    login(admin, "admin", "Sup3rSecure!x")
    page = admin.get("/service").text
    token = re.search(r'name="_csrf" value="([^"]*)"', page).group(1)
    resp = admin.post("/service/action",
                      data={"_csrf": token, "action": "start"},
                      follow_redirects=False)
    assert resp.status_code == 200
    assert calls["kind"] == "gateway_start"
    assert "gateway_action.py" in calls["command"]
    assert "已提交" in resp.text


def test_gateway_stop_requires_confirm_word(admin, monkeypatch):
    """危险动作仍要确认词；确认后同样走后台任务。"""
    calls = {}

    def fake_submit(kind, command, *, shell=True, cwd=None):
        calls["kind"] = kind
        return 12

    monkeypatch.setattr(installer, "submit", fake_submit)
    login(admin, "admin", "Sup3rSecure!x")
    page = admin.get("/service").text
    token = re.search(r'name="_csrf" value="([^"]*)"', page).group(1)
    bad = admin.post("/service/action",
                     data={"_csrf": token, "action": "stop", "confirm": "瞎写"},
                     follow_redirects=False)
    assert bad.status_code == 400 and "确认词" in bad.text
    ok = admin.post("/service/action",
                    data={"_csrf": token, "action": "stop", "confirm": "停止"},
                    follow_redirects=False)
    assert ok.status_code == 200
    assert calls["kind"] == "gateway_stop"


def test_gateway_driver_script_is_valid():
    """驱动脚本必须可编译（py_compile），且包含 supervisor 调用（自动注册链路依托它）。"""
    import py_compile

    from app.core.settings import settings

    cmd = installer.gateway_action_job("start")
    script = settings.jobs_dir / "gateway_action.py"
    py_compile.compile(str(script), doraise=True)
    text = script.read_text(encoding="utf-8")
    assert "supervisor" in text and "detect" in text
    assert "gateway_action.py" in cmd


def test_job_output_reaches_console_and_log(monkeypatch):
    """任务输出必须双通道：终端实时回显（用户能看见在跑）+ 日志文件落盘。"""
    import io
    import time as _time

    from app.core import db

    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    cmd = f'"{sys.executable}" -c "print(\'tee-line-1\'); print(\'tee-line-2\')"'
    job_id = installer.submit("test_tee", cmd, shell=True)
    deadline = _time.monotonic() + 20
    while _time.monotonic() < deadline:
        row = db.query_one("SELECT status FROM job_runs WHERE id = ?", (job_id,))
        if row and row["status"] != "running":
            break
        _time.sleep(0.2)
    assert any("tee-line-1" in line for line in installer.job_log(job_id))
    assert "tee-line-1" in captured.getvalue()


def test_cancel_running_job_kills_tree_and_settles_state():
    """取消必须把整个进程树杀掉，并把状态/日志收尾（卡死任务的自救口子）。"""
    import time as _time

    from app.core import db

    cmd = f'"{sys.executable}" -c "import time; time.sleep(60)"'
    job_id = installer.submit("install", cmd, shell=True)
    deadline = _time.monotonic() + 10
    while _time.monotonic() < deadline and job_id not in installer._procs:
        _time.sleep(0.1)
    assert job_id in installer._procs, "任务进程未登记，无法取消"
    assert installer.cancel(job_id) == "已取消任务"

    deadline = _time.monotonic() + 15
    row = None
    while _time.monotonic() < deadline:
        row = db.query_one(
            "SELECT status, exit_code FROM job_runs WHERE id = ?", (job_id,))
        if row and row["status"] != "running":
            break
        _time.sleep(0.2)
    assert row["status"] == "failed"
    assert row["exit_code"] is not None
    assert any("手动取消" in line for line in installer.job_log(job_id))


def test_cancel_endpoint_shows_notice(admin, monkeypatch):
    """取消端点对普通表单提交也要有可见反馈（notice 横幅）。"""
    monkeypatch.setattr(installer, "cancel", lambda job_id: "已取消任务")
    login(admin, "admin", "Sup3rSecure!x")
    page = admin.get("/service").text
    token = re.search(r'name="_csrf" value="([^"]*)"', page).group(1)
    resp = admin.post("/service/job/cancel",
                      data={"_csrf": token, "job_id": "1"},
                      follow_redirects=False)
    assert resp.status_code == 200
    assert "已取消任务" in resp.text


def test_install_health_reports_missing_pieces(tmp_path):
    """安装完整性体检：半成品（有二进制、缺 venv）不能报"全部完成"。"""
    from app.hermes.paths import HermesPaths

    home = tmp_path / "home"
    home.mkdir()
    stub = home / "bin" / "hermes"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.touch()
    # 只有二进制、没有 hermes-agent 仓库/venv
    health = installer.install_health(HermesPaths(home=home, bin=str(stub)))
    by_label = {c["label"]: c for c in health}
    assert by_label["可执行文件（hermes）"]["ok"] is True
    assert by_label["源码与虚拟环境"]["ok"] is False
    assert by_label["源码与虚拟环境"]["hint"]
    # 补齐仓库 + venv 后转绿
    repo = home / "hermes-agent"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "pyproject.toml").touch()
    py = repo / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.touch()
    health2 = installer.install_health(HermesPaths(home=home, bin=str(stub)))
    by2 = {c["label"]: c for c in health2}
    assert by2["源码与虚拟环境"]["ok"] is True


def test_browser_installed_detection(tmp_path, monkeypatch):
    """浏览器引擎探测：目录为空 → False；出现 chromium-* → True。"""
    if sys.platform == "win32":
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        base = tmp_path / "ms-playwright"
    else:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        base = tmp_path / ".cache" / "ms-playwright"
    assert installer.browser_installed() is False
    (base / "chromium-1234").mkdir(parents=True)
    assert installer.browser_installed() is True


def test_job_panel_fragment_live_and_cancellable(admin):
    """运行中任务的片段：带轮询 + 带取消按钮 + 日志可见。"""
    from app.core import db
    from app.core.settings import settings

    db.execute("INSERT INTO job_runs (kind, command, log_path, status) "
               "VALUES (?,?,?,?)",
               ("install", "cmd", "jobs/job-x.log", "running"))
    job_id = db.query_one("SELECT id FROM job_runs ORDER BY id DESC LIMIT 1")["id"]
    (settings.jobs_dir / f"job-{job_id}.log").write_text(
        "line1\nline2\n", encoding="utf-8")
    login(admin, "admin", "Sup3rSecure!x")
    frag = admin.get("/service/job").text
    assert "取消任务" in frag
    assert 'hx-get="/service/job"' in frag and "every 2s" in frag
    assert "line2" in frag


def test_install_card_advertises_auto_browser_chain():
    """安装卡片必须写明"装完自动补装浏览器组件"（核心能力，默认必装）。"""
    from pathlib import Path

    tpl = (Path(__file__).resolve().parent.parent / "app" / "web"
           / "templates" / "service.html").read_text(encoding="utf-8")
    assert "自动补装" in tpl and "浏览器组件" in tpl
    assert 'name="skip_browser"' not in tpl   # 不再给用户"跳过核心能力"的默认勾选


def test_browser_backfill_rejects_when_not_installed(admin):
    """未装 Hermes（或无安装目录）时，补装按钮必须给出人话拒绝而不是报错页。"""
    login(admin, "admin", "Sup3rSecure!x")
    page = admin.get("/service").text
    token = re.search(r'name="_csrf" value="([^"]*)"', page).group(1)
    resp = admin.post("/service/browser", data={"_csrf": token},
                      follow_redirects=False)
    assert resp.status_code == 400
    assert "未找到" in resp.text


@pytest.mark.skipif(sys.platform == "win32", reason="补装脚本仅 POSIX 提供")
def test_browser_backfill_submits_smart_script(admin, hermes_home, monkeypatch):
    """补装走"官方→国内镜像"双源脚本，且脚本真的落在 jobs 目录里。"""
    from app.hermes import paths as paths_module
    from app.hermes.paths import HermesPaths

    repo = hermes_home.home / "hermes-agent"
    repo.mkdir(parents=True, exist_ok=True)
    stub = hermes_home.home / "bin" / "hermes"
    stub.parent.mkdir(parents=True, exist_ok=True)
    stub.touch()
    paths_module.set_override(HermesPaths(home=hermes_home.home, bin=str(stub)))
    try:
        calls = {}

        def fake_submit(kind, command, *, shell=True, cwd=None):
            calls["kind"] = kind
            calls["command"] = command
            return 3

        monkeypatch.setattr(installer, "submit", fake_submit)
        login(admin, "admin", "Sup3rSecure!x")
        page = admin.get("/service").text
        # 安装完整性体检常驻可见：装到哪一步、缺什么、怎么补，不靠猜
        assert "安装完整性" in page
        assert "可执行文件（hermes）" in page
        assert "源码与虚拟环境" in page
        assert "安装 / 补装组件" in page
        assert "继续 / 修复安装" in page
        token = re.search(r'name="_csrf" value="([^"]*)"', page).group(1)
        resp = admin.post("/service/browser", data={"_csrf": token},
                          follow_redirects=False)
        assert resp.status_code == 200
        assert calls["kind"] == "browser_install"
        assert "browser_install.sh" in calls["command"]
        script = (installer.settings.jobs_dir / "browser_install.sh").read_text(
            encoding="utf-8")
        assert "cdn.npmmirror.com/binaries/playwright" in script
        assert "timeout 300" in script
        assert "PLAYWRIGHT_DOWNLOAD_HOST" in script
        # Ubuntu 新版兼容构建重试（对齐官方安装器逻辑）
        assert "PLAYWRIGHT_HOST_PLATFORM_OVERRIDE" in script
        # Browser Use CLI（浏览器自动化默认后端）也在脚本里补齐
        assert "tool install browser-use" in script
        assert "pypi.tuna.tsinghua.edu.cn" in script
        # 国内镜像脚本 core-only 会跳过 Node：缺 npx 时从 npmmirror 补装
        assert "npmmirror.com/mirrors/node" in script
        # 折中通道：camofox（npm+npmmirror）、语音依赖（uv pip+清华）、
        # 系统件（apt）、Computer Use 驱动（GitHub 加速镜像兜底）
        assert "camofox-browser" in script
        assert "wake,voice" in script
        assert "build-essential" in script
        assert "ghfast.top" in script
    finally:
        paths_module.set_override(None)
