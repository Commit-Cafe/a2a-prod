"""MiniMax Agent CLI 入口。

用法：
    python -m agents.minimax_agent
    python -m agents.minimax_agent --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import argparse
import sys

from agents.base_agent import run_agent_cli
from agents.minimax_agent.agent import MiniMaxAgent, MiniMaxAgentSettings


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="agents.minimax_agent",
        description="a2a-prod MiniMax Agent (ADK + a2a-sdk)",
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

    settings = MiniMaxAgentSettings()
    agent = MiniMaxAgent(settings=settings)

    if _known.host is not None or _known.port is not None:
        import asyncio

        asyncio.run(agent.run(host=_known.host, port=_known.port))
    else:
        run_agent_cli(agent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
