"""Trace 装饰器（P4 阶段，详见 ADR-0008 / SPEC §3.8.3）。

``@trace_node(name="...")`` 用于装饰 LangGraph 节点函数或任意同步/异步函数：

- 自动包装为 Langfuse span（含 trace）
- 自动注入 ``request_id`` / ``session_id`` / ``user_id`` 三个 metadata
- 函数抛异常时 span 标记为 error 并 reraise
- 未启用 Langfuse 时退化为 no-op（不抛异常）

使用：

.. code-block:: python

   from observability.tracing import trace_node

   @trace_node(name="orchestrator.route")
   def route_node(state: AgentState) -> dict:
       ...
"""

from __future__ import annotations

import functools
import inspect
import logging
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


_METADATA_KEYS: tuple[str, ...] = ("request_id", "session_id", "user_id")


def _extract_metadata(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, str]:
    """从函数参数中提取 metadata。

    约定（优先级从高到低）：
    1. 关键字参数 ``request_id`` / ``session_id`` / ``user_id`` 直接取
    2. 位置参数：第一个若是 dict（LangGraph state）也取其对应键
    3. 都没有则返回 ``{}``，Langfuse 写入时显式 metadata 留空
    """
    meta: dict[str, str] = {}
    for key in _METADATA_KEYS:
        if key in kwargs and isinstance(kwargs[key], str):
            meta[key] = kwargs[key]
    if args and isinstance(args[0], dict):
        state: dict[str, Any] = args[0]
        for key in _METADATA_KEYS:
            if key not in meta and isinstance(state.get(key), str):
                meta[key] = str(state[key])
    return meta


def trace_node(  # noqa: C901 - 复杂但清晰
    name: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """装饰器：把函数包装为 Langfuse span。

    :param name: span 名称，默认用 ``module.qualname``
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        span_name = name or f"{func.__module__}.{func.__qualname__}"

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
                from observability.langfuse_client import get_langfuse_client

                client = get_langfuse_client()
                if client is None:
                    return await func(*args, **kwargs)  # type: ignore[no-any-return]

                meta = _extract_metadata(args, kwargs)
                with client.start_as_current_observation(as_type="span", name=span_name) as span:
                    for k, v in meta.items():
                        span.update(metadata={k: v})
                    try:
                        result = await func(*args, **kwargs)
                    except Exception as exc:
                        span.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")
                        raise
                    return result  # type: ignore[no-any-return]

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            from observability.langfuse_client import get_langfuse_client

            client = get_langfuse_client()
            if client is None:
                return func(*args, **kwargs)

            meta = _extract_metadata(args, kwargs)
            with client.start_as_current_observation(as_type="span", name=span_name) as span:
                for k, v in meta.items():
                    span.update(metadata={k: v})
                try:
                    result = func(*args, **kwargs)
                except Exception as exc:
                    span.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")
                    raise
                return result

        return sync_wrapper

    return decorator
