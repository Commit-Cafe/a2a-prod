"""GLM Agent CLI 入口。

用法：
    python -m agents.glm_agent
    python -m agents.glm_agent --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse
import sys

from agents.base_agent import run_agent_cli
from agents.glm_agent.agent import GLMAgent, GLMAgentSettings


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="agents.glm_agent",
        description="a2a-prod GLM Agent (ADK + a2a-sdk)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="HTTP 监听地址（默认走 settings.host / 0.0.0.0）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP 监听端口（默认走 settings.port / 8000）",
    )
    # argparse 允许透传未识别参数给 settings，避免重复定义
    _known, _unknown = parser.parse_known_args()

    settings = GLMAgentSettings()
    agent = GLMAgent(settings=settings)

    # 直接走 BaseAgent.run；asyncio 由 run_agent_cli 处理
    if _known.host is not None or _known.port is not None:
        # 异步入口需要手动 wrap
        import asyncio

        asyncio.run(agent.run(host=_known.host, port=_known.port))
    else:
        run_agent_cli(agent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
