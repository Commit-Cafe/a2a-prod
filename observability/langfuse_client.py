"""Langfuse 客户端封装（P4 阶段，详见 ADR-0008 / SPEC §3.8）。

核心 API（4 个）：
- ``is_langfuse_enabled()``：通过 ``LANGFUSE_PUBLIC_KEY`` 探活；缺失视为禁用
- ``get_langfuse_client()``：返回 Langfuse 单例（不抛异常，client disabled 时返回 None）
- ``setup_otlp_env()``：从 env 派生 ``OTEL_EXPORTER_OTLP_ENDPOINT`` + ``OTEL_EXPORTER_OTLP_HEADERS``
  （防御 [GitHub issue #9871](https://github.com/langfuse/langfuse/issues/9871) 401）
- ``health_check()``：包装 ``client.auth_check()``，失败返回 ``False`` 而不抛异常

设计原则：
- 所有 helper 都不抛异常（探活场景需要 graceful degradation）
- PK/SK 一律走 env（``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` / ``LANGFUSE_HOST``）
- 调用方在 Agent 启动时调 ``health_check()``，失败则容器退出非 0
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


_REQUIRED_ENV_VARS: tuple[str, ...] = (
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_HOST",
)


def is_langfuse_enabled() -> bool:
    """是否启用 Langfuse 客户端。

    判定标准：``LANGFUSE_PUBLIC_KEY`` 非空。host / secret 缺失也视为未启用。
    """
    return all(os.environ.get(var) for var in _REQUIRED_ENV_VARS)


def setup_otlp_env() -> None:
    """派生 OTEL env 变量（防御 GitHub issue #9871 401 错误）。

    - ``OTEL_EXPORTER_OTLP_ENDPOINT`` = ``${LANGFUSE_HOST}/api/public/otel``
    - ``OTEL_EXPORTER_OTLP_HEADERS`` = ``Authorization=Basic ${base64(PK:SK)}``

    该函数幂等；多次调用结果一致。Agent 启动时调一次即可。

    已知问题：未启用 Langfuse 时不抛异常（让 Agent 在 trace 不可用时仍能启动），
    仅打 INFO 日志提示。
    """
    if not is_langfuse_enabled():
        logger.info("Langfuse 未启用，跳过 OTEL env 设置")
        return

    host = os.environ["LANGFUSE_HOST"].rstrip("/")
    pk = os.environ["LANGFUSE_PUBLIC_KEY"]
    sk = os.environ["LANGFUSE_SECRET_KEY"]

    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = f"{host}/api/public/otel"

    auth_token = base64.b64encode(f"{pk}:{sk}".encode()).decode("ascii")
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization=Basic {auth_token}"

    logger.info("Langfuse OTEL env 已设置：endpoint=%s", os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"])


def get_langfuse_client() -> Any:
    """获取 Langfuse 客户端单例（langfuse.get_client()）。

    未启用时返回 None，调用方需自己处理 None 场景。
    """
    if not is_langfuse_enabled():
        logger.warning("Langfuse 未启用（缺 %s），返回 None", "/".join(_REQUIRED_ENV_VARS))
        return None

    from langfuse import get_client as _sdk_get_client  # 延迟导入

    return _sdk_get_client()


def health_check() -> bool:
    """探活：调 ``client.auth_check()``，失败返回 False 而不抛异常。

    失败场景（Graceful degradation）：
    - 客户端未启用（env 缺失）→ False + 提示日志
    - SDK 调用抛异常（网络 / 401 / 500）→ False + 错误日志
    """
    client = get_langfuse_client()
    if client is None:
        return False

    try:
        client.auth_check()
    except Exception as exc:  # noqa: BLE001 - 探活吞所有异常
        logger.error("Langfuse health_check 失败：%s: %s", type(exc).__name__, exc)
        return False

    return True
