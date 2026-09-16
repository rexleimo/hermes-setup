#!/usr/bin/env bash
# 重启 Hermes Console 并跑上线自检。任何 Python/模板改动后必须执行本脚本，
# 否则运行中的进程仍是旧代码（曾因此导致"修了但用户没拿到"）。
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8420}"

pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1
nohup uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT" > data/server.log 2>&1 &

for i in $(seq 1 20); do
  if curl -sf -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null \
     || curl -sf -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
    break
  fi
  sleep 0.5
done

echo "== 服务已重启 (port $PORT)，运行自检 =="
uv run python scripts/check_live.py "http://127.0.0.1:$PORT"
