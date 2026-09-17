# Hermes Console — Docker 一键运行（全新环境开箱即用）
#
# 构建：docker build -t hermes-console .
# 运行（HERMES_CONSOLE_SECRET 必填，先用下面命令生成）：
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
#   docker run -d -p 8420:8420 \
#     -e HERMES_CONSOLE_SECRET="<上一步生成的密钥>" \
#     -v hermes-console-data:/data -v hermes-home:/hermes \
#     --name hermes-console hermes-console
# 之后浏览器打开 http://localhost:8420，完成管理员初始化，
# 再到「运行体检」确认全绿 → 「服务管理」一键安装 Hermes。
#
# 国内构建加速：
#   docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t hermes-console .
#
# 说明：Hermes 本体不在镜像里（保持镜像精简），首次在控制台点"一键安装"，
# 装进 /hermes 卷；数据目录在 /data 卷。删容器不丢数据。
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_COMPILE_BYTECODE=1 \
    HERMES_CONSOLE_DATA=/data \
    HERMES_HOME=/hermes

ARG PIP_INDEX_URL=""
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl git bash ca-certificates \
 && rm -rf /var/lib/apt/lists \
 && (test -n "$PIP_INDEX_URL" && pip install --no-cache-dir -i "$PIP_INDEX_URL" uv \
     || pip install --no-cache-dir uv)

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY app/ ./app/
RUN uv sync --frozen --no-dev \
 && useradd -m appuser \
 && mkdir -p /data /hermes \
 && chown -R appuser:appuser /app /data /hermes

USER appuser
VOLUME ["/data", "/hermes"]
EXPOSE 8420
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -f http://127.0.0.1:8420/healthz || exit 1
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8420"]
