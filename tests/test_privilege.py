"""安装权限探测 + sudo 密码通道（后台任务无 TTY，密码提示会卡死任务）。

背景：部分 Linux 账号非 root、也非免密 sudo——需要输密码。此前安装链路对
提权能力零判断：sudo 的密码提示在后台（stdin 已断开）只能卡死或静默失败。
现在：页面探测并展示权限；需密码账号在表单里填 sudo 密码；任务经 sudo -S
一次性非交互认证（密码只进 0600 临时文件，不进命令行/日志/审计），失败 1s 内
明确报错（exit 3）。以下用例锁死这些行为。
"""
from __future__ import annotations

import re
import sys

import pytest

from app.hermes import installer
from tests.conftest import login


# ---------------------------------------------------------------------------
# 权限分类
# ---------------------------------------------------------------------------

def test_classify_privilege_matrix():
    assert installer._classify_privilege(0, False, False) == "root"
    assert installer._classify_privilege(0, True, True) == "root"      # root 优先
    assert installer._classify_privilege(1000, False, False) == "nosudo"
    assert installer._classify_privilege(1000, True, True) == "passwordless"
    assert installer._classify_privilege(1000, True, False) == "password"


def test_detect_privilege_windows_mode(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    info = installer.detect_privilege(force=True)
    assert info["mode"] == "windows"
    assert info["needs_password"] is False
    assert "label" in info and info["label"]


def _stub_linux_sudo(monkeypatch, passwordless_rc: int):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("os.geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr("shutil.which",
                        lambda name: "/usr/bin/sudo" if name == "sudo" else None)
    import subprocess as sp
    monkeypatch.setattr(sp, "run",
                        lambda *a, **k: sp.CompletedProcess(a, passwordless_rc))


def test_detect_privilege_linux_needs_password(monkeypatch):
    """非 root + 有 sudo + 非免密 → 需密码（页面要出现密码输入框）。"""
    _stub_linux_sudo(monkeypatch, passwordless_rc=1)   # sudo -n 失败
    info = installer.detect_privilege(force=True)
    assert info["mode"] == "password"
    assert info["needs_password"] is True


def test_detect_privilege_passwordless(monkeypatch):
    _stub_linux_sudo(monkeypatch, passwordless_rc=0)   # sudo -n 成功
    info = installer.detect_privilege(force=True)
    assert info["mode"] == "passwordless"
    assert info["needs_password"] is False


# ---------------------------------------------------------------------------
# 安装命令：sudo 驱动包装
# ---------------------------------------------------------------------------

def test_install_command_without_password_is_plain():
    cmd = installer.install_command(None)
    assert cmd == installer.preferred_install()["cmd"]
    assert installer.install_command("") == installer.preferred_install()["cmd"]


def test_install_command_with_password_wraps_driver(monkeypatch, tmp_path):
    """提供密码（POSIX）：命令换成 sudo 驱动脚本；密码只落 0600 文件，
    不出现在命令字符串与脚本内容里（命令行/日志/DB 都不可见）。"""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(type(installer.settings), "jobs_dir",
                        property(lambda self: tmp_path))

    password = "S3cret!sudo"
    cmd = installer.install_command(password)

    assert cmd != installer.preferred_install()["cmd"]
    m = re.match(r'^bash "([^"]+)"$', cmd)
    assert m, f"驱动命令应为 bash 脚本调用：{cmd}"
    script = tmp_path / m.group(1).rsplit("/", 1)[-1]
    assert script.exists()
    content = script.read_text(encoding="utf-8")
    assert password not in cmd and password not in content   # 密码不落命令行/脚本
    assert "sudo -S" in content                              # 非交互认证
    assert "exit 3" in content                               # 认证失败快速退出
    assert "--skip-setup" in content                         # 原安装命令原样执行

    # 密码文件：存在、内容正确
    pf_line = re.search(r'PASSFILE="([^"]+)"', content).group(1)
    pf = tmp_path / pf_line.rsplit("/", 1)[-1]
    assert pf.exists()
    assert pf.read_text(encoding="utf-8") == password + "\n"


def test_install_command_password_ignored_on_windows(monkeypatch, tmp_path):
    """Windows 原生通道无 sudo 概念：密码被忽略，命令保持官方 PS 通道。"""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(type(installer.settings), "jobs_dir",
                        property(lambda self: tmp_path))
    assert installer.install_command("whatever") == installer.preferred_install()["cmd"]


# ---------------------------------------------------------------------------
# 浏览器补装：run_sudo 通道
# ---------------------------------------------------------------------------

def test_browser_script_uses_run_sudo_with_passfile():
    assert "run_sudo()" in installer.BROWSER_SCRIPT
    assert 'HERMES_SUDO_PASS_FILE' in installer.BROWSER_SCRIPT
    assert "sudo -n" in installer.BROWSER_SCRIPT          # 无密码时仍退回免密探测
    # 旧的硬门槛（只有免密才装系统依赖）应已移除
    assert "SUDO=\"sudo -n\"" not in installer.BROWSER_SCRIPT


def test_browser_job_passfile_injection(hermes_home, monkeypatch, tmp_path):
    """补装命令：无密码时不注入；有密码时经 HERMES_SUDO_PASS_FILE 注入 0600 文件。"""
    import dataclasses

    from app.hermes import paths as paths_module

    # 伪造已安装状态：可执行文件 + 仓库目录（resolve_agent_repo 认 pyproject.toml）
    home = hermes_home.home
    (home / "bin").mkdir(parents=True, exist_ok=True)
    exe = home / "bin" / "hermes"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    (home / "hermes-agent").mkdir(exist_ok=True)
    (home / "hermes-agent" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    paths_module.set_override(dataclasses.replace(hermes_home, bin=str(exe)))

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(type(installer.settings), "jobs_dir",
                        property(lambda self: tmp_path))

    plain = installer.browser_install_job()
    assert "HERMES_SUDO_PASS_FILE" not in plain

    injected = installer.browser_install_job("p@ss")
    assert injected.startswith('HERMES_SUDO_PASS_FILE="')
    m = re.match(r'HERMES_SUDO_PASS_FILE="([^"]+)" bash ', injected)
    assert m
    pf = tmp_path / m.group(1).rsplit("/", 1)[-1]
    assert pf.exists() and pf.read_text(encoding="utf-8") == "p@ss\n"


# ---------------------------------------------------------------------------
# Web 链路：sudo_pass 表单字段 + 审计不留密码
# ---------------------------------------------------------------------------

def test_install_endpoint_accepts_sudo_pass(logged_in, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(type(installer.settings), "jobs_dir",
                        property(lambda self: tmp_path))
    calls = {}
    monkeypatch.setattr(installer, "submit",
                        lambda kind, cmd, **kw: calls.update(kind=kind, cmd=cmd) or 1)

    from app.core import db
    page = logged_in.get("/service")
    token = re.search(r'name="_csrf" value="([^"]*)"', page.text).group(1)
    password = "AuditMe!99"
    resp = logged_in.post("/service/install",
                          data={"_csrf": token, "sudo_pass": password},
                          follow_redirects=False)
    assert resp.status_code == 200
    assert calls["kind"] == "install"
    assert calls["cmd"] != installer.preferred_install()["cmd"]  # 走了 sudo 驱动

    rows = db.query("SELECT detail FROM audit_log WHERE action='job_install'")
    assert rows
    assert password not in rows[0]["detail"]               # 审计不留密码明文
    assert "sudo 密码" in rows[0]["detail"]


def test_service_page_shows_privilege_section(admin):
    """页面按探测结果如实渲染：需密码/无 sudo 时出现提示与密码框，其余不出现。"""
    login(admin, "admin", "Sup3rSecure!x")
    mode = installer.detect_privilege(force=True)["mode"]
    page = admin.get("/service")
    assert page.status_code == 200
    text = page.text
    if mode == "password":
        assert "需要 sudo 密码" in text
        assert 'name="sudo_pass"' in text          # 安装/补装表单带密码输入框
    elif mode == "nosudo":
        assert "当前账号无 sudo" in text
    else:
        assert 'name="sudo_pass"' not in text      # root/免密/Windows 不出密码框
