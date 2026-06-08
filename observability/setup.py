"""P4 trace 启动入口（详见 ADR-0008 / SPEC §3.8.3）。

两套 setup：
- ``setup_agent()``：在 3 Agent 启动时调（base_agent.run_agent_cli() 内），
  包含 ``setup_otlp_env()`` + ``GoogleADKInstrumentor().instrument()``
- ``setup_litellm()``：在 LiteLLM 启动时调，把 ``langfuse_otel`` 加到 ``litellm.callbacks``
  （也兼容通过环境变量 ``LITELLM_CALLBACKS=langfuse_otel`` 注入）

未启用 Langfuse 时两个 setup 都安全 no-op。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def setup_agent() -> None:
    """Agent 启动入口：setup_otlp_env + GoogleADKInstrumentor.instrument。

    调用方：``base_agent.run_agent_cli()`` 第一行。

    注意：``GoogleADKInstrumentor`` 必须在 import google.adk 之后调用，
    因此 base_agent.py 模块级不应有 ``import google.adk``，保持延迟加载。
    """
    from observability.langfuse_client import is_langfuse_enabled, setup_otlp_env

    if not is_langfuse_enabled():
        logger.info("Langfuse 未启用，Agent 跳过 trace 接入（继续正常工作）")
        return

    setup_otlp_env()

    try:
        from openinference.instrumentation.google_adk import GoogleADKInstrumentor

        GoogleADKInstrumentor().instrument()
        logger.info("GoogleADKInstrumentor 已接入 trace")
    except ImportError as exc:
        logger.error("openinference-instrumentation-google-adk 未安装：%s", exc)
    except Exception as exc:  # noqa: BLE001 - 启动期不能崩
        logger.error("GoogleADKInstrumentor 接入失败：%s: %s", type(exc).__name__, exc)


def setup_litellm() -> None:
    """LiteLLM 启动入口：把 ``langfuse_otel`` 加到 ``litellm.callbacks``。

    调用方：LiteLLM 容器启动脚本。

    与环境变量 ``LITELLM_CALLBACKS=langfuse_otel`` 等价；本函数的存在是为了
    显式 import 链路 + 错误处理。
    """
    from observability.langfuse_client import is_langfuse_enabled, setup_otlp_env

    if not is_langfuse_enabled():
        logger.info("Langfuse 未启用，LiteLLM 跳过 trace 接入")
        return

    setup_otlp_env()

    try:
        import litellm

        if "langfuse_otel" not in litellm.callbacks:
            litellm.callbacks = list(litellm.callbacks) + ["langfuse_otel"]
        logger.info("LiteLLM langfuse_otel callback 已注入")
    except ImportError as exc:
        logger.error("litellm 未安装：%s", exc)
    except Exception as exc:  # noqa: BLE001 - 启动期不能崩
        logger.error("LiteLLM trace 接入失败：%s: %s", type(exc).__name__, exc)
