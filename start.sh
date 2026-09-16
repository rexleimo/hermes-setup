#!/usr/bin/env bash
# ============================================================
# Hermes Console 一键启动（macOS / Linux）
# 首次运行自动装依赖；启动成功后自动打开浏览器。Ctrl+C 停止。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8420}"

if ! command -v uv >/dev/null 2>&1; then
  echo "[提示] 未检测到 uv，正在安装（官方脚本）..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "[1/2] 准备依赖（首次约 1 分钟）..."
uv sync --quiet

echo "[2/2] 启动控制台 http://127.0.0.1:${PORT} （Ctrl+C 停止）"
# 3 秒后自动打开浏览器（macOS open / Linux xdg-open，有哪个用哪个）
( sleep 3
  if command -v open >/dev/null 2>&1; then open "http://127.0.0.1:${PORT}"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:${PORT}"
  fi ) &

exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
