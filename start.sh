#!/usr/bin/env bash
# ============================================================
# Hermes Console 一键启动（macOS / Linux）
# 首次运行自动装依赖；启动成功后自动打开浏览器。Ctrl+C 停止。
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"
PORT="${PORT:-8420}"
ISSUES="https://github.com/rexleimo/hermes-setup/issues"

if ! command -v uv >/dev/null 2>&1; then
  echo "[提示] 未检测到 uv，正在安装..."
  # 官方脚本（astral.sh）下载失败不直接退出：回退到 PyPI 安装（国内可达）
  TMP_SH="$(mktemp)"
  if curl -fsSL --connect-timeout 15 https://astral.sh/uv/install.sh -o "$TMP_SH" 2>/dev/null \
     && sh "$TMP_SH" 2>/dev/null; then
    :
  else
    echo "[提示] 官方脚本下载失败（多为网络原因），改从 PyPI 安装..."
    if ! python3 -m pip install --user -q uv 2>/dev/null \
       && ! python -m pip install --user -q uv 2>/dev/null; then
      cat <<EOF
[失败] 无法自动安装 uv，多半是网络不通或缺少 Python。
  · 方案 1：开代理后重新运行本脚本；
  · 方案 2：用国内镜像手动安装 uv：
      python3 -m pip install -U uv -i https://pypi.tuna.tsinghua.edu.cn/simple
  · 方案 3：先安装 Python：https://www.python.org/downloads/
  仍失败请到 ${ISSUES} 反馈（附本窗口截图）。
EOF
      rm -f "$TMP_SH"
      exit 1
    fi
  fi
  rm -f "$TMP_SH"
  export PATH="$HOME/.local/bin:$PATH"
  # macOS pip --user 的脚本目录随 Python 版本不同，逐个并入 PATH
  for d in "$HOME"/Library/Python/*/bin; do
    [ -d "$d" ] && export PATH="$d:$PATH"
  done
fi

echo "[1/2] 准备依赖（首次约 1 分钟）..."
if ! uv sync --quiet; then
  cat <<EOF
[失败] 依赖安装出错，最常见原因是访问 PyPI 网络不畅。
  可改用国内镜像重试：
      UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple ./start.sh
  仍失败请到 ${ISSUES} 反馈（附本窗口截图）。
EOF
  exit 1
fi

echo "[2/2] 启动控制台 http://127.0.0.1:${PORT} （Ctrl+C 停止）"
# 3 秒后自动打开浏览器（macOS open / Linux xdg-open，有哪个用哪个）
( sleep 3
  if command -v open >/dev/null 2>&1; then open "http://127.0.0.1:${PORT}"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://127.0.0.1:${PORT}"
  fi ) &

exec uv run uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
