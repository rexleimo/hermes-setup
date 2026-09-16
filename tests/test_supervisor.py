"""supervisor / installer 单测：用桩 hermes 二进制验证进程控制与状态解析。"""
from __future__ import annotations

import os
import stat

import pytest

from app.hermes import supervisor
from app.hermes.paths import HermesPaths

# 桩 hermes 是 /bin/sh 脚本，只能 POSIX 下执行
posix_only = pytest.mark.skipif(os.name != "posix", reason="需要 POSIX shebang 脚本执行")


def make_stub_bin(tmp_path, status_output="gateway is running (pid 42424)",
                  status_code=0):
    bin_path = tmp_path / "hermes"
    bin_path.write_text(
        "#!/bin/sh\n"
        f'if [ "$1 $2" = "gateway status" ]; then echo "{status_output}"; exit {status_code}; fi\n'
        'if [ "$1 $2" = "gateway start" ]; then echo "gateway started"; exit 0; fi\n'
        'if [ "$1 $2" = "gateway stop" ]; then echo "gateway stopped"; exit 0; fi\n'
        'if [ "$1 $2" = "gateway restart" ]; then echo "gateway restarted"; exit 0; fi\n'
        'if [ "$1" = "--version" ]; then echo "hermes 2026.9.1-test"; exit 0; fi\n'
        "exit 1\n",
        encoding="utf-8")
    bin_path.chmod(bin_path.stat().st_mode | stat.S_IEXEC)
    return str(bin_path)


def _paths(tmp_path, bin_path) -> HermesPaths:
    home = tmp_path / "h"
    home.mkdir(parents=True, exist_ok=True)
    return HermesPaths(home=home, bin=bin_path)


@posix_only
def test_status_running_via_cli(tmp_path):
    paths = _paths(tmp_path, make_stub_bin(tmp_path))
    st = supervisor.status(paths)
    assert st.running is True
    assert st.pid == 42424
    assert supervisor.version(paths) == "hermes 2026.9.1-test"


@posix_only
def test_status_stopped(tmp_path):
    paths = _paths(tmp_path, make_stub_bin(tmp_path, "gateway is not running"))
    st = supervisor.status(paths)
    assert st.running is False


@posix_only
def test_start_stop_restart(tmp_path):
    paths = _paths(tmp_path, make_stub_bin(tmp_path))
    assert "started" in supervisor.start(paths)
    assert "stopped" in supervisor.stop(paths)
    assert "restarted" in supervisor.restart(paths)


def test_missing_bin_raises(tmp_path):
    paths = _paths(tmp_path, None)
    import pytest

    with pytest.raises(supervisor.SupervisorError):
        supervisor.start(paths)


def test_tail_log(tmp_path):
    home = tmp_path / "h2"
    home.mkdir()
    log = home / "logs" / "gateway.log"
    log.parent.mkdir()
    log.write_text("\n".join(f"line {i}" for i in range(300)) + "\n", encoding="utf-8")
    lines = supervisor.tail_log(log, 50)
    assert len(lines) == 50
    assert lines[-1] == "line 299"


def test_env_passthrough_is_minimal():
    from app.hermes.supervisor import _child_env

    os.environ["SOME_SECRET_FOR_TEST"] = "x"
    env = _child_env()
    assert "SOME_SECRET_FOR_TEST" not in env
    assert "PATH" in env
