"""Hermes 目录与文件路径探测。

参考官方布局：
  POSIX:   ~/.hermes/（config.yaml / .env / hermes-agent/ / logs/）
  Windows: %LOCALAPPDATA%\\hermes（官方 install.ps1 默认落点）
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core import appsettings


@dataclass(frozen=True)
class HermesPaths:
    home: Path
    bin: str | None

    @property
    def config_yaml(self) -> Path:
        return self.home / "config.yaml"

    @property
    def env_file(self) -> Path:
        return self.home / ".env"

    @property
    def logs_dir(self) -> Path:
        return self.home / "logs"

    @property
    def gateway_log(self) -> Path:
        return self.logs_dir / "gateway.log"

    @property
    def errors_log(self) -> Path:
        return self.logs_dir / "errors.log"

    @property
    def agent_repo(self) -> Path:
        return self.home / "hermes-agent"

    @property
    def installed(self) -> bool:
        return self.bin is not None and Path(self.bin).exists()

    @property
    def initialized(self) -> bool:
        return self.config_yaml.exists()


def which_hermes() -> str | None:
    for candidate in ("hermes",):
        found = shutil.which(candidate)
        if found:
            return found
    # 常见用户级安装位置
    extras = ["~/.local/bin/hermes", "/usr/local/bin/hermes", "/opt/homebrew/bin/hermes"]
    if sys.platform == "win32":
        # 官方 PS 安装器把 hermes.exe 落到 %LOCALAPPDATA%\hermes\bin，
        # 但安装后用户级 PATH 更新只对新进程生效，已运行的控制台进程 PATH 是旧的——
        # 必须用绝对路径兜底，否则"装完也显示未安装"，直到重启控制台。
        local_app = os.environ.get("LOCALAPPDATA", "")
        extras.append(str(Path(local_app) / "hermes" / "bin" / "hermes.exe"))
    for extra in extras:
        p = Path(extra).expanduser()
        if p.exists():
            return str(p)
    return None


# 模块级 override：测试或特殊部署可固定路径。
# detect() 是稳定的包装函数 —— 其他模块 `from paths import detect` 拿到的
# 引用虽然在导入时固化，但包装函数每次调用都会读取 override，不存在串味问题。
_override: HermesPaths | None = None


def set_override(paths: HermesPaths | None) -> None:
    global _override
    _override = paths


def _default_home() -> Path:
    """全新机默认家目录：与官方各平台安装器的落点一致。
    Windows 官方 install.ps1 默认就是 $env:LOCALAPPDATA\\hermes；
    POSIX 走 ~/.hermes。DB/环境变量显式配置永远优先。"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "hermes"
    return Path.home() / ".hermes"


def detect() -> HermesPaths:
    if _override is not None:
        return _override
    home_raw = appsettings.get_setting("hermes_home") or _default_home()
    home = Path(home_raw).expanduser().resolve()
    bin_override = appsettings.get_setting("hermes_bin")
    bin_path = bin_override or which_hermes()
    return HermesPaths(home=home, bin=bin_path)


def resolve_agent_repo(paths: HermesPaths) -> Path | None:
    """定位 hermes-agent 源码目录：直连路径优先，否则从可执行文件反推。

    root/FHS 安装把仓库放在 /usr/local/lib/hermes-agent（而非家目录下），
    且 /usr/local/bin/hermes 可能是软链或 bash 包裹脚本——逐级上找、
    包裹脚本 exec 行解析、官方固定落点三层兜底，否则 MCP/技能/插件目录
    与 venv 探测在 root 机上全部落空。
    """
    direct = paths.agent_repo
    if direct.exists():
        return direct
    if paths.bin:
        real = Path(paths.bin).resolve()
        for cand in (real.parent, real.parent.parent, real.parent.parent.parent):
            if _looks_like_repo(cand):
                return cand
        shim_repo = _repo_from_shim(real)
        if shim_repo is not None:
            return shim_repo
    if sys.platform != "win32":
        for cand in _FHS_REPO_CANDIDATES:
            p = Path(cand)
            if _looks_like_repo(p):
                return p
    return None


# root/FHS 安装的固定落点（官方 install.sh：root 走 /usr/local/lib）。
_FHS_REPO_CANDIDATES = ("/usr/local/lib/hermes-agent", "/opt/hermes-agent")

# 官方 root 安装的 bin 是 bash 包裹脚本而非软链，形如：
#   exec "/usr/local/lib/hermes-agent/venv/bin/python" "/usr/local/lib/hermes-agent/hermes" "$@"
_SHIM_RE = re.compile(r'"([^"]+)/venv/bin/python"\s+"([^"]+)/hermes"')


def _looks_like_repo(p: Path) -> bool:
    try:
        return (p / "pyproject.toml").exists() or (p / "gateway").is_dir()
    except OSError:
        return False


def _repo_from_shim(path: Path) -> Path | None:
    """从官方 bash 包裹脚本的 exec 行反推仓库目录（带标记校验，防误判）。"""
    try:
        if path.stat().st_size > 65536:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _SHIM_RE.search(text)
    if not m:
        return None
    # 注意：group(2) 经贪婪回溯后恰好就是仓库目录本身（字面量吃掉了末尾 /hermes"），
    # 不要再 .parent()——否则会指到仓库的上级。
    repo = Path(m.group(2))
    return repo if _looks_like_repo(repo) else None
