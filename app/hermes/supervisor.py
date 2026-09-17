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
            # hermes CLI 输出 UTF-8；中文 Windows 默认 GBK 会在解码线程直接报错、
            # 把输出吞成空串，状态探测因此永远「未知」。固定按 UTF-8 读。
            encoding="utf-8", errors="replace",
            env={**_child_env(), "PATH": _path_with_common_bins()},
        )
    except subprocess.TimeoutExpired as exc:
        raise SupervisorError(f"命令超时：hermes {' '.join(args)}") from exc
    except OSError as exc:
        raise SupervisorError(f"无法执行 hermes：{exc}") from exc
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


def _path_with_common_bins() -> str:
    """构造子进程 PATH：常见安装位置 + 继承的系统 PATH。

    必须用 os.pathsep（Windows 是 ';'）拼接；早期用 ':' 在 Windows 上会把整个
    PATH 拼成一个非法条目，导致 `hermes gateway status` 找不到 node。
    """
    import os

    extras = [str(p) for p in _extra_bins()]
    inherited = os.environ.get("PATH", "")
    parts = list(dict.fromkeys([*extras, *inherited.split(os.pathsep)]))
    return os.pathsep.join(p for p in parts if p)


def _extra_bins() -> list[Path]:
    """只补真实存在的目录（不存在的条目会污染子进程查找）。"""
    import os
    import sys

    home = Path.home()
    if sys.platform == "win32":
        candidates = [home / "AppData" / "Roaming" / "npm",
                      Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs",
                      home / ".bun" / "bin"]
    else:
        candidates = [Path("/usr/local/bin"), Path("/opt/homebrew/bin"),
                      home / ".local" / "bin", Path("/usr/bin"), Path("/bin")]
        nvm_base = home / ".nvm" / "versions" / "node"
        if nvm_base.exists():
            versions = sorted(nvm_base.glob("*/bin"), key=lambda p: p.name)
            if versions:
                candidates.append(versions[-1])
        candidates += [home / ".volta" / "bin", home / ".asdf" / "shims",
                       home / ".bun" / "bin"]
    return [c for c in candidates if c.exists()]


def _child_env() -> dict[str, str]:
    """给 hermes 子进程可用环境：继承完整环境，仅剔除控制台自身敏感键。

    不能再用 POSIX keep 白名单：Windows 子进程依赖 SYSTEMROOT/USERPROFILE/
    LOCALAPPDATA 等，缺失会让 hermes CLI 无法解析家目录
    （RuntimeError: Could not determine home directory），
    Gateway 状态探测与启停全部失败。
    """
    import os

    return {k: v for k, v in os.environ.items()
            if not k.upper().startswith("HERMES_CONSOLE")}


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
                           ("not running", "stopped", "inactive", "未运行", "已停止",
                            "no gateway process", "not started", "exited", "未启动", "没有运行"))
            positive = any(k in text for k in ("running", "active", "is up", "已启动"))
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
    import sys

    if sys.platform == "win32":
        # Windows 没有 signal.kill；且 os.kill(pid, 0) 在 Windows 上会变成
        # TerminateProcess（直接杀掉目标进程！），绝不可用。改走 OpenProcess 查询。
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)

    try:
        import signal as _signal
        _signal.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ---------------------------------------------------------------------------
# 控制动作
# ---------------------------------------------------------------------------

def _service_missing(out: str) -> bool:
    """识别"网关服务尚未注册"这一特定失败（Linux 首次使用 / 换机）：
    hermes gateway start 会输出 '✗ Gateway service is not installed'
    + 'Run: hermes gateway install'。"""
    low = (out or "").lower()
    return "not installed" in low and "gateway install" in low


def start(paths: HermesPaths | None = None) -> str:
    paths = paths or detect()
    code, out = _run_cli(paths, "gateway", "start")
    if code != 0 and _service_missing(out):
        # 首启自动注册服务（幂等）：{user scope} systemd 单元 + linger，
        # 目标是把"先 install 再 start"这类术语挡在小白视线之外。
        icode, iout = _run_cli(paths, "gateway", "install")
        if icode != 0:
            raise SupervisorError(f"网关服务注册失败（hermes gateway install）：{iout or out}")
        code, out = _run_cli(paths, "gateway", "start")
        out = f"{iout}\n{out}".strip()
    if code != 0:
        raise SupervisorError(_explain_failure(out) or f"hermes gateway start 退出码 {code}")
    # 退出码 0 但进程秒退（安全护栏拒绝启动）也要说清楚，不能让小白面对沉默的「状态未知」
    if "refusing to start" in out.lower() or "exiting cleanly" in out.lower():
        raise SupervisorError(_explain_failure(out))
    return out or "网关启动指令已下发"


_GUARD_HINTS = (
    ("refusing to start", "渠道安全护栏拦截了启动：某个渠道白名单为空但策略是 open。"
     "微信渠道重新扫一次码即可自动修复（接入助手会自动把号主写进白名单）；"
     "其他渠道请在渠道页配好白名单后重试。"),
    ("no such file or directory", "找不到启动文件，建议到「安装与更新」重新安装。"),
)


def _explain_failure(out: str) -> str:
    low = (out or "").lower()
    for key, hint in _GUARD_HINTS:
        if key in low:
            return f"{out}\n\n【控制台解读】{hint}" if out else hint
    return out or ""


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
        # 某些版本没有 restart / 服务尚未注册：退化为 stop+start
        # （stop 在"未注册"时也会报同样的话，忽略它，交给 start 自动注册）
        try:
            stop(paths)
        except SupervisorError:
            pass
        return start(paths)
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
