#!/usr/bin/env bash
# ============================================================
# Hermes Console 重启（Linux / macOS）
# 用法：./restart.sh          # 用默认端口重启
#      PORT=9000 ./restart.sh # 用指定端口重启
# 重启顺序：优先走 docker compose（线上标准部署），
#          兜底为「杀掉占端口的进程 → 重新 ./start.sh」。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-${HERMES_CONSOLE_PORT:-8420}}"

echo "[restart] 准备重启 Hermes Console（端口 ${PORT}）..."

# ---- 情形 A：docker compose 部署（推荐）-----------------------------
if command -v docker >/dev/null 2>&1 && [ -f docker-compose.yml ]; then
  echo "[restart] 检测到 docker-compose，执行容器重启..."
  docker compose -f docker-compose.yml restart hermes-console
  echo "[restart] 已重启容器，查看日志：docker compose -f docker-compose.yml logs -f hermes-console"
  exit 0
fi

# ---- 情形 B：start.sh 直接运行 -------------------------------------
if command -v start.sh >/dev/null 2>&1 || [ -x ./start.sh ]; then
  # 杀掉当前占用端口的进程（主要是 uvicorn）
  echo "[restart] 尝试释放端口 ${PORT} 上的进程..."
  pids="$(lsof -ti tcp:${PORT} 2>/dev/null || true)"
  if [ -n "${pids}" ]; then
    echo "[restart] 正在终止 PID: ${pids}"
    kill ${pids} 2>/dev/null || true
    sleep 2
    # 仍活着的强杀
    pids="$(lsof -ti tcp:${PORT} 2>/dev/null || true)"
    [ -n "${pids}" ] && kill -9 ${pids} 2>/dev/null || true
  else
    echo "[restart] 未检测到端口占用，无需杀进程。"
  fi
  echo "[restart] 启动 ./start.sh ..."
  exec ./start.sh
fi

echo "[restart] 未检测到 docker-compose 或 start.sh，无法自动重启。"
echo "         请手动重启运行控制台的服务进程。"
exit 1
