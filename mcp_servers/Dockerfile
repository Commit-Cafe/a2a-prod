# syntax=docker/dockerfile:1.7
# ============================================================
# a2a-prod MCP Server 通用 Dockerfile（P3 引入）
# 三 MCP server（filesystem / fetch / shell）共享此文件，
# 通过 build-arg MCP_MODULE 决定运行哪个 server。
#
# 构建示例：
#   docker build -t a2a-prod-mcp-filesystem \
#     --build-arg MCP_MODULE=mcp_servers.filesystem \
#     -f mcp_servers/Dockerfile .
#
# 运行（见 infra/docker-compose.yml）：
#   docker run -p 12101:12101 -e MCP_FILESYSTEM_PORT=12101 a2a-prod-mcp-filesystem
# ============================================================

ARG PYTHON_VERSION=3.12-slim

FROM python:${PYTHON_VERSION} AS base

# 容器内非 root 用户
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/a2a

ARG UV_VERSION=0.4.30
RUN pip install --no-cache-dir uv==${UV_VERSION}

WORKDIR /app
RUN useradd --create-home --shell /bin/bash a2a \
    && mkdir -p /app \
    && chown -R a2a:a2a /app

# MCP server 只需要 mcp_servers/ + scripts/ + pyproject.toml
COPY --chown=a2a:a2a pyproject.toml README.md ./
COPY --chown=a2a:a2a mcp_servers ./mcp_servers
COPY --chown=a2a:a2a scripts ./scripts

# 用 root 装依赖（mcp + httpx + html2text 由 pyproject.toml 拉入）
RUN uv pip install --system --no-cache "." \
    && python -c "from mcp.server.fastmcp import FastMCP; print('mcp ok')"

USER a2a

# 复用 scripts/healthcheck.py，HEALTHCHECK_URL 由 compose 注入
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python /app/scripts/healthcheck.py || exit 1

# build-arg 决定运行哪个 MCP 模块
ARG MCP_MODULE=mcp_servers.filesystem
ENV MCP_MODULE=${MCP_MODULE}

EXPOSE 12101 12102 12103

# python -m ${MCP_MODULE} → __main__.py → main() → mcp.run(streamable-http)
CMD python -m ${MCP_MODULE}
