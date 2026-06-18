# syntax=docker/dockerfile:1.7
# ============================================================
# a2a-prod Agent 通用 Dockerfile
# 三 Agent（GLM / DeepSeek / MiniMax）共享此文件，
# 通过 build-arg AGENT_MODULE 决定运行哪个 agent。
#
# 构建示例：
#   docker build -t a2a-prod-glm-agent \
#     --build-arg AGENT_MODULE=agents.glm_agent \
#     -f agents/Dockerfile .
#
# 运行（见 infra/docker-compose.yml）：
#   docker run -p 12001:8000 -e PORT=8000 a2a-prod-glm-agent
# ============================================================

ARG PYTHON_VERSION=3.12-slim

FROM python:${PYTHON_VERSION} AS base

# 容器内非 root 用户（ADK / a2a-sdk 默认假设可写 $HOME）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/a2a

# 装 uv（轻量、快），不装整 pip 链
ARG UV_VERSION=0.4.30
RUN pip install --no-cache-dir uv==${UV_VERSION}

# 创建工作目录与非 root 用户
WORKDIR /app
RUN useradd --create-home --shell /bin/bash a2a \
    && mkdir -p /app \
    && chown -R a2a:a2a /app

# 拷项目源码（.dockerignore 应排除 .venv-prod / build / dist / .git）
COPY --chown=a2a:a2a pyproject.toml README.md ./
COPY --chown=a2a:a2a agents ./agents
COPY --chown=a2a:a2a host ./host
COPY --chown=a2a:a2a observability ./observability
COPY --chown=a2a:a2a orchestrator ./orchestrator
COPY --chown=a2a:a2a scripts ./scripts

# 用 root 装 Python 依赖（--system 装到 /usr/local/lib/python3.12/site-packages/）
# 必须用 root：非 root 用户没有写 /usr/local 的权限
# 注：USER a2a 移到 install 之后，避免 P0 阶段遇到的 Permission denied (os error 13)
RUN uv pip install --system --no-cache "." \
    && python -c "import google.adk; import a2a; print('adk+a2a ok')"

# 装完依赖再切换非 root 用户
USER a2a

# 健康检查（HEALTHCHECK_URL 由 compose 注入）
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python /app/scripts/healthcheck.py || exit 1

# build-arg 决定运行哪个 agent 模块
ARG AGENT_MODULE=agents.glm_agent
ENV AGENT_MODULE=${AGENT_MODULE}

# 默认端口 8000（容器内）；映射到主机 12001/12002/12003 由 compose 控制
ENV PORT=8000
EXPOSE 8000

# P0-5 实装后：python -m ${AGENT_MODULE} 会调用 __main__.py → run_agent_cli
# → asyncio.run(agent.run()) → uvicorn 启动 A2AStarletteApplication
# ${AGENT_MODULE} 由 build-arg 注入：agents.glm_agent / agents.deepseek_agent / agents.minimax_agent
# 注：用 shell form（非 exec form），否则 ${AGENT_MODULE} 不会被 /bin/sh 展开
CMD python -m ${AGENT_MODULE}
