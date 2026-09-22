#!/usr/bin/env bash
# ============================================================
# Hermes Console 重启（面向实际部署：nohup ./start.sh 后台启动）
#
# 你们的启动方式：  nohup ./start.sh >> log.txt 2>&1 &
# 重启逻辑：        先 kill 掉旧服务进程（uvicorn）→ 再 nohup 起新的。
# 用法：            ./restart.sh
#                  PORT=9000 ./restart.sh        # 改端口
#                  HERMES_CONSOLE_LOG=app.log ./restart.sh   # 改日志名
# 说明：start.sh 末尾是 exec uv ... uvicorn，shell 会被替换，
#       所以后台只剩 uvicorn app.main:app 一个进程，按它杀最准、不伤别的。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-${HERMES_CONSOLE_PORT:-8420}}"
LOG="${HERMES_CONSOLE_LOG:-hermes-console.log}"

echo "[restart] 准备重启 Hermes Console（端口 ${PORT}，日志 ${LOG}）"

# ---- 1. 杀掉旧服务进程（先按命令名，再按端口兜底）--------------------
kill_server() {
  # 杀 uvicorn 本体
  local pids
  pids="$(pgrep -f "uvicorn app.main:app" 2>/dev/null || true)"
  if [ -n "${pids}" ]; then
    echo "[restart] 终止旧服务 PID: ${pids}"
    kill ${pids} 2>/dev/null || true
  fi
  # 兜底：端口仍占用就按端口强杀
  if command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -ti tcp:"${PORT}" 2>/dev/null || true)"
    if [ -n "${pids}" ]; then
      echo "[restart] 按端口 ${PORT} 强杀残留 PID: ${pids}"
      kill -9 ${pids} 2>/dev/null || true
    fi
  fi
}

kill_server
sleep 2
kill_server     # 残留再补一刀

# ---- 2. nohup 后台重启 ---------------------------------------------
echo "[restart] 启动新服务..."
nohup ./start.sh >> "${LOG}" 2>&1 &
disown 2>/dev/null || true

# ---- 3. 确认起来 ---------------------
sleep 4
if pgrep -f "uvicorn app.main:app" >/dev/null 2>&1; then
  echo "[restart] 已重启，服务已在运行。实时日志：tail -f ${LOG}"
else
  echo "[restart] 服务未拉起，查看日志：tail -n 50 ${LOG}"
fi
