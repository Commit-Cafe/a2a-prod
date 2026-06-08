# LiteLLM 容器镜像（基于官方镜像 + 注入 Langfuse trace 依赖）
# syntax=docker/dockerfile:1.7

FROM ghcr.io/berriai/litellm:main-stable

USER root

# 安装 uv（LiteLLM 镜像不自带 pip/uv，仅 npm + 预装 venv）
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# 用 uv 装 Langfuse SDK + OTEL（详见 ADR-0008）
ENV PATH="/root/.local/bin:${PATH}"
RUN /root/.local/bin/uv pip install --python /app/.venv/bin/python --no-cache \
    "langfuse>=3.0.0,<4.0.0" \
    "opentelemetry-api>=1.33.1,<2.0.0" \
    "opentelemetry-sdk>=1.33.1,<2.0.0" \
    "opentelemetry-exporter-otlp-proto-http>=1.33.1,<2.0.0"

# 复制 trace 启动包装（仅 litellm_entrypoint.py + setup.py + langfuse_client.py 必需）
COPY observability/litellm_entrypoint.py /app/observability/litellm_entrypoint.py
COPY observability/setup.py /app/observability/setup.py
COPY observability/langfuse_client.py /app/observability/langfuse_client.py
COPY observability/__init__.py /app/observability/__init__.py

# 通过自定义 entrypoint 注入 trace
CMD ["/app/.venv/bin/python", "-m", "observability.litellm_entrypoint", "--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0"]
