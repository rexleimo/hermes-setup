"""启动脚本契约：HOST/PORT 环境变量覆盖 + 默认回环 + 非回环警告。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8-sig")


def test_start_sh_host_port_override():
    sh = _read("start.sh")
    assert 'HOST="${HOST:-${HERMES_CONSOLE_HOST:-0.0.0.0}}"' in sh
    assert 'PORT="${PORT:-${HERMES_CONSOLE_PORT:-8420}}"' in sh
    assert '--host "$HOST"' in sh
    assert "BROWSE_HOST" in sh  # 浏览器打开回环地址（部分浏览器打不开 0.0.0.0）
    assert "HERMES_CONSOLE_SECRET" in sh  # 非回环警告必须提到密钥
    assert "HERMES_CONSOLE_ALLOWED_IPS" in sh  # …以及白名单


def test_start_bat_host_port_override():
    bat = _read("start.bat")
    assert "if not defined HOST set HOST=0.0.0.0" in bat
    assert "--host %HOST%" in bat
    assert "BROWSE_HOST" in bat
    assert "HERMES_CONSOLE_SECRET" in bat
    assert "HERMES_CONSOLE_ALLOWED_IPS" in bat


def test_app_default_bind_all_interfaces():
    """应用默认监听所有接口（含回环）。要求测试环境未导出该变量（CI 默认干净）。"""
    import os

    assert os.environ.get("HERMES_CONSOLE_HOST") is None
    from app.core.settings import settings

    assert settings.host == "0.0.0.0"
