"""LiteLLM 容器启动入口（带 Langfuse trace 注入）。

Docker 容器内执行的命令（见 infra/docker-compose.yml 的 litellm service）：

.. code-block:: sh

    python -m observability.litellm_entrypoint --config /app/infra/litellm/config.yaml

行为：
1. 调 ``setup_litellm()`` 把 ``langfuse_otel`` 加到 litellm.callbacks
2. exec 真正的 ``litellm --config ...`` 启动 Proxy

未启用 Langfuse 时 step 1 是 no-op，行为等价于直接跑 litellm。
"""

from __future__ import annotations

import argparse
import logging
import sys

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="LiteLLM Proxy 启动包装（注入 Langfuse trace）",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="litellm config.yaml 路径",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=4000,
        help="litellm 监听端口（默认 4000）",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="litellm 监听地址（默认 0.0.0.0）",
    )
    args = parser.parse_args()

    # 1. 注入 trace（no-op 如果未启用 Langfuse）
    try:
        from observability.setup import setup_litellm

        setup_litellm()
    except Exception as e:  # noqa: BLE001 - 启动期不能崩
        logger.warning("setup_litellm 失败，继续启动 LiteLLM：%s", e)

    # 2. exec litellm CLI（避免包装带来的 signal / PID 隔离问题）
    #
    # S10 修复演进（GLM 2026-06-18 review4 后）：
    #   旧实现 ``run_server(config=..., port=..., host=...)`` 在新版 LiteLLM
    #   (main-stable) 里失效——``run_server`` 现在是 Click ``Command`` 对象，
    #   以函数方式调用时 Click 仍会读 ``sys.argv``，把当前解释器路径当成多余位置参数，
    #   报 ``Got unexpected extra argument (/app/.venv/bin/python)``。
    #
    #   正解：``os.execvp`` 直接接管进程，用 CLI 参数启动 ``litellm``，把控制权
    #   完全交给 Click。这也与 docstring 所述 "exec 真正的 litellm" 一致。
    import os

    cli = "litellm"
    cli_args = [
        cli,
        "--config", args.config,
        "--port", str(args.port),
        "--host", args.host,
    ]
    logger.info("启动 LiteLLM Proxy（execvp）：%s", " ".join(cli_args))
    os.execvp(cli, cli_args)  # 正常情况下不会 return
    return 0  # 仅为类型完整；execvp 成功则不达此行


if __name__ == "__main__":
    sys.exit(main())
