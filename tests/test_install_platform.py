"""Windows 一键安装走原生通道 + 全新机默认家目录/检测回归。

背景：控制台曾在所有平台跑 `curl | bash` 装 install.sh（Linux/macOS 专用脚本），
Windows 上 bash 解析到 WSL 存根，装不出原生 hermes；且默认家目录 ~/.hermes
与官方 PS 安装器落点（%LOCALAPPDATA%\\hermes）错位。以下用例锁死这两处。
"""
from __future__ import annotations

import sys
from urllib.parse import urlparse

from app.hermes import installer
from app.hermes import paths as paths_mod


def test_install_cmd_platform_contract():
    """安装命令必须与当前平台原生通道一致，且后台无 TTY 必须跳过交互。"""
    if sys.platform == "win32":
        assert "powershell" in installer.INSTALL_CMD.lower()
        assert "install.ps1" in installer.INSTALL_CMD
        assert "-SkipSetup" in installer.INSTALL_CMD
        assert "-NonInteractive" in installer.INSTALL_CMD
        assert "PowerShell" in installer.INSTALL_METHOD_LABEL
    else:
        assert "install.sh" in installer.INSTALL_CMD
        assert "bash" in installer.INSTALL_CMD
        assert "--skip-setup" in installer.INSTALL_CMD


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
    from tests.conftest import login

    login(admin, "admin", "Sup3rSecure!x")
    resp = admin.get("/service")
    assert resp.status_code == 200
    assert installer.INSTALL_METHOD_LABEL in resp.text
    needle = "install.ps1" if sys.platform == "win32" else "install.sh"
    assert needle in resp.text
