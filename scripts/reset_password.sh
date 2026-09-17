#!/usr/bin/env bash
# ============================================================
# 忘记管理员密码？运行本脚本按提示重置（macOS / Linux）
# Windows 用户：双击项目目录里的 reset-password.bat
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."
echo "=== Hermes Console 管理员密码重置 ==="
echo "（重置后该账号的所有登录会话都会下线）"
read -r -p "要重置的用户名（直接回车默认 admin）：" USERNAME
USERNAME="${USERNAME:-admin}"
if ! command -v uv >/dev/null 2>&1; then
  echo "[提示] 未检测到 uv。请先运行一次 ./start.sh（它会自动安装 uv），再回来运行本脚本。"
  exit 1
fi
uv run python scripts/console_admin.py reset-password "$USERNAME"
echo "用新密码回到浏览器登录即可。"
