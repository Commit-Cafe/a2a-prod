# LiteLLM 容器镜像（基于官方镜像 + 注入 Langfuse trace 依赖）
# syntax=docker/dockerfile:1.7

FROM ghcr.io/berriai/litellm:main-stable

USER root

# 关键：base image（ghcr.io/berriai/litellm:main-stable）自带 ENTRYPOINT
# ``[docker/prod_entrypoint.sh]``，该脚本会 ``exec litellm "$@"``——即把我们
# 定义的 CMD 当作 ``litellm`` CLI 的参数拼装。若不清空，我们的 Python entrypoint
# 命令（``/app/.venv/bin/python -m observability.litellm_entrypoint ...``）会被
# 当成 ``litellm`` 的多余位置参数，报 ``Got unexpected extra argument``。
ENTRYPOINT []

# 新版 LiteLLM base image（main-stable）既不含 uv 也不含 pip，仅 /app/.venv/bin/python。
# 用 venv 自带的 ensurepip bootstrap 出 pip，再用 pip 装依赖（详见 ADR-0008）。
# 不再用 `curl | sh` 装 uv —— 那条路在新 image 下 PATH/安装位置不稳定，会导致 /root/.local/bin/uv 找不到。
RUN /app/.venv/bin/python -m ensurepip --upgrade \
    && /app/.venv/bin/python -m pip install --no-cache-dir --upgrade pip \
    && /app/.venv/bin/python -m pip install --no-cache-dir \
        "langfuse>=3.0.0,<4.0.0" \
        "opentelemetry-api>=1.33.1,<2.0.0" \
        "opentelemetry-sdk>=1.33.1,<2.0.0" \
        "opentelemetry-exporter-otlp-proto-http>=1.33.1,<2.0.0" \
        "structlog>=24.0.0"

# 复制整个 observability 包：litellm_entrypoint 是入口，但 __init__.py 会 import
# tracing.trace_node，setup.py/langfuse_client.py 也互相引用，故整包 COPY 保持一致。
COPY observability/ /app/observability/

# 通过自定义 entrypoint 注入 trace
CMD ["/app/.venv/bin/python", "-m", "observability.litellm_entrypoint", "--config", "/app/config.yaml", "--port", "4000", "--host", "0.0.0.0"]
