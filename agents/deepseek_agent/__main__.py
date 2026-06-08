"""DeepSeek Agent CLI 入口。

用法：
    python -m agents.deepseek_agent
    python -m agents.deepseek_agent --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse
import sys

from agents.base_agent import run_agent_cli
from agents.deepseek_agent.agent import DeepSeekAgent, DeepSeekAgentSettings


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="agents.deepseek_agent",
        description="a2a-prod DeepSeek Agent (ADK + a2a-sdk)",
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
    _known, _unknown = parser.parse_known_args()

    settings = DeepSeekAgentSettings()
    agent = DeepSeekAgent(settings=settings)

    if _known.host is not None or _known.port is not None:
        import asyncio

        asyncio.run(agent.run(host=_known.host, port=_known.port))
    else:
        run_agent_cli(agent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
