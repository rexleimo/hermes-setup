"""Hermes 目录与文件路径探测。

参考官方布局（~/.hermes/）：
  config.yaml      核心配置
  .env             API Key / 渠道令牌
  hermes-agent/    安装的 git 仓库
  logs/gateway.log, logs/errors.log
"""
from __future__ import annotations

import os
import shutil
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
    for extra in ("~/.local/bin/hermes", "/usr/local/bin/hermes", "/opt/homebrew/bin/hermes"):
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


def detect() -> HermesPaths:
    if _override is not None:
        return _override
    home_raw = appsettings.get_setting("hermes_home") or os.path.expanduser("~/.hermes")
    home = Path(home_raw).expanduser().resolve()
    bin_override = appsettings.get_setting("hermes_bin")
    bin_path = bin_override or which_hermes()
    return HermesPaths(home=home, bin=bin_path)
