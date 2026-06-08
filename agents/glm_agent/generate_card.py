"""启动时把动态生成的 Agent Card 写到 ``agent.json``（SPEC §1.1）。

用法：
    python -m agents.glm_agent.generate_card > agents/glm_agent/agent.json
    python -m agents.glm_agent.generate_card --path agents/glm_agent/agent.json

P0-5 阶段：占位 ``agent.json`` 留作静态 fallback，运行时仍以 ``BaseAgent.build_card()``
返回的动态版本为准（容器内的 ``/.well-known/agent.json`` 端点）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agents.glm_agent.agent import GLMAgent


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 GLM Agent 静态 Agent Card")
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="输出路径（默认：stdout）",
    )
    parser.add_argument(
        "--public-url",
        default="http://localhost:12001/",
        help="Agent 对外 URL（生产环境改成真实域名）",
    )
    args = parser.parse_args()

    agent = GLMAgent()
    card = agent.build_card(public_url=args.public_url)
    payload = json.dumps(card.model_dump(exclude_none=True), ensure_ascii=False, indent=2)

    if args.path is None:
        sys.stdout.write(payload + "\n")
    else:
        args.path.write_text(payload + "\n", encoding="utf-8")
        print(f"wrote {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
