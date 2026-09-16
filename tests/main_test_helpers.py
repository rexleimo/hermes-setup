"""测试辅助：构造干净的 Hermes 家目录样例。"""
from __future__ import annotations

from pathlib import Path

SAMPLE_CONFIG = """# Hermes Agent 核心配置（测试样例，含注释）
# 由 Hermes Console 管理

model:
  # 主模型设置
  provider: auto
  default: gpt-5.2

terminal:
  backend: local   # local | docker | ssh

platforms:
  telegram:
    enabled: false
"""

SAMPLE_ENV = """# Hermes secrets
EXISTING_KEY="hello"
"""


def fresh_hermes_home(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(SAMPLE_CONFIG, encoding="utf-8")
    (home / ".env").write_text(SAMPLE_ENV, encoding="utf-8")
