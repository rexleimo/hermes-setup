"""Hermes Gateway 服务进程管理。

优先走官方 CLI（`hermes gateway start|stop|restart|status`），由 Hermes 自身
完成守护化；状态探测综合 CLI 输出、状态文件与日志新鲜度三路信号。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.hermes.paths import HermesPaths, detect

CLI_TIMEOUT = 20


class SupervisorError(Exception):
    pass


@dataclass
class GatewayStatus:
    running: bool | None          # None = 无法判定
    detail: str = ""
    pid: int | None = None
    log_fresh_seconds: float | None = None
    sources: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        if self.running is True:
            return "运行中"
        if self.running is False:
            return "已停止"
        return "未知"


def _run_cli(paths: HermesPaths, *args: str) -> tuple[int, str]:
    if not paths.bin:
        raise SupervisorError("未找到 hermes 可执行文件，请先安装或到「系统设置」指定路径")
    try:
        proc = subprocess.run(
            [paths.bin, *args],
            capture_output=True, text=True, timeout=CLI_TIMEOUT,
            env={**_child_env(), "PATH": _path_with_common_bins()},
        )
    except subprocess.TimeoutExpired as exc:
        raise SupervisorError(f"命令超时：hermes {' '.join(args)}") from exc
    except OSError as exc:
        raise SupervisorError(f"无法执行 hermes：{exc}") from exc
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


def _path_with_common_bins() -> str:
    """构造子进程 PATH：常见安装位置 + 用户 node 版本管理器 + 继承的系统 PATH。

    很多用户的 hermes 启动器依赖 node（nvm/volta 安装），默认 PATH 里没有会导致
    `hermes gateway status` 报 "exec: node: not found"。
    """
    import os

    extras = _extra_bins()
    inherited = os.environ.get("PATH", "")
    inherited_parts = inherited.split(":") if inherited else []
    parts = list(dict.fromkeys([*extras, *inherited_parts]))
    return ":".join(p for p in parts if p)


def _extra_bins() -> list[str]:
    import os

    home = Path.home()
    extras = ["/usr/local/bin", "/opt/homebrew/bin", str(home / ".local" / "bin"),
              "/usr/bin", "/bin"]
    nvm_base = home / ".nvm" / "versions" / "node"
    if nvm_base.exists():
        versions = sorted(nvm_base.glob("*/bin"), key=lambda p: p.name)
        if versions:
            extras.append(str(versions[-1]))
    for candidate in (home / ".volta" / "bin", home / ".asdf" / "shims",
                      home / ".bun" / "bin"):
        if candidate.exists():
            extras.append(str(candidate))
    return extras


def _child_env() -> dict[str, str]:
    """给 hermes 子进程一个干净但可用的环境（避免继承控制台自身的敏感变量）。"""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TMPDIR", "USER", "SHELL")
    return {k: v for k, v in __import__("os").environ.items() if k in keep}


# ---------------------------------------------------------------------------
# 状态探测
# ---------------------------------------------------------------------------

def status(paths: HermesPaths | None = None) -> GatewayStatus:
    paths = paths or detect()
    st = GatewayStatus(running=None)

    # 信号 1：官方 CLI status
    if paths.bin:
        try:
            code, out = _run_cli(paths, "gateway", "status")
            text = out.lower()
            st.sources["cli"] = out[:400]
            # 注意顺序：先匹配否定词（"not running" 包含 "running"）
            negative = any(k in text for k in
                           ("not running", "stopped", "inactive", "未运行", "已停止"))
            positive = any(k in text for k in ("running", "active"))
            if negative:
                st.running = False
            elif positive:
                st.running = True
            m = re.search(r"pid[:\s]+(\d+)", text)
            if m:
                st.pid = int(m.group(1))
            st.detail = out.strip().splitlines()[-1] if out.strip() else st.detail
        except SupervisorError as exc:
            st.sources["cli"] = str(exc)

    # 信号 2：gateway_state.json
    state_file = paths.home / "gateway_state.json"
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            st.sources["state_file"] = str(data)[:400]
            pid = data.get("pid")
            if isinstance(pid, int) and _pid_alive(pid):
                st.running = True
                st.pid = st.pid or pid
        except (OSError, ValueError):
            pass

    # 信号 3：gateway.log 新鲜度（60 秒内有写入 → 大概率在跑）
    if paths.gateway_log.exists():
        age = time.time() - paths.gateway_log.stat().st_mtime
        st.log_fresh_seconds = round(age, 1)
        if age < 60 and st.running is None:
            st.running = True

    if st.running is None and not paths.installed:
        st.running = False
        st.detail = st.detail or "Hermes 尚未安装"
    return st


def _pid_alive(pid: int) -> bool:
    try:
        import os
        import signal as _signal
        _signal.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ---------------------------------------------------------------------------
# 控制动作
# ---------------------------------------------------------------------------

def start(paths: HermesPaths | None = None) -> str:
    paths = paths or detect()
    code, out = _run_cli(paths, "gateway", "start")
    if code != 0:
        raise SupervisorError(out or f"hermes gateway start 退出码 {code}")
    return out or "网关启动指令已下发"


def stop(paths: HermesPaths | None = None) -> str:
    paths = paths or detect()
    code, out = _run_cli(paths, "gateway", "stop")
    if code != 0:
        raise SupervisorError(out or f"hermes gateway stop 退出码 {code}")
    return out or "网关已停止"


def restart(paths: HermesPaths | None = None) -> str:
    paths = paths or detect()
    code, out = _run_cli(paths, "gateway", "restart")
    if code != 0:
        # 某些版本没有 restart，退化为 stop+start
        try:
            stop(paths)
            return start(paths)
        except SupervisorError:
            raise SupervisorError(out or "restart 失败") from None
    return out or "网关已重启"


def version(paths: HermesPaths | None = None) -> str | None:
    paths = paths or detect()
    if not paths.bin:
        return None
    try:
        code, out = _run_cli(paths, "--version")
        return out.strip().splitlines()[-1] if out.strip() and code == 0 else None
    except SupervisorError:
        return None


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def tail_log(path: Path, lines: int = 200) -> list[str]:
    if not path.exists():
        return []
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            block = 8192
            data = b""
            while size > 0 and data.count(b"\n") <= lines:
                step = min(block, size)
                size -= step
                fh.seek(size)
                data = fh.read(step) + data
            return [l for l in data.decode("utf-8", "replace").splitlines()[-lines:] if l.strip()]
    except OSError:
        return []
