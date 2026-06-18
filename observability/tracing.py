"""Trace 装饰器（P4 阶段，详见 ADR-0008 / SPEC §3.8.3）。

``@trace_node(name="...")`` 用于装饰 LangGraph 节点函数或任意同步/异步函数：

- 自动包装为 Langfuse span（含 trace）
- 自动注入 ``request_id`` / ``session_id`` / ``user_id`` 三个 metadata
- 函数抛异常时 span 标记为 error 并 reraise
- 未启用 Langfuse 时退化为 no-op（不抛异常）

层级关系：
- ``start_trace()`` 创建顶级 trace，返回 trace context dict
- ``@trace_node`` 装饰的函数从第一个 dict 参数的 ``_trace_context`` key
  取出 parent context，在其下创建子 span
- 若无 ``_trace_context``，则退化为独立 span（向后兼容）

使用：

.. code-block:: python

   from observability.tracing import trace_node, start_trace

   @trace_node(name="orchestrator.route")
   def route_node(state: AgentState) -> dict:
       ...
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

import structlog

logger = structlog.get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

_METADATA_KEYS: tuple[str, ...] = ("request_id", "session_id", "user_id")
_TRACE_CONTEXT_KEY = "_trace_context"


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


def _get_trace_context(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """从函数参数中提取 _trace_context。

    优先级：
    1. kwargs 中的 _trace_context
    2. 第一个位置参数（dict）中的 _trace_context key
    """
    ctx = kwargs.get(_TRACE_CONTEXT_KEY)
    if ctx is not None:
        return ctx
    if args and isinstance(args[0], dict):
        ctx = args[0].get(_TRACE_CONTEXT_KEY)
        if ctx is not None:
            return ctx
    return None


def start_trace(
    name: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """创建顶级 Langfuse trace 并返回 context dict。

    返回值应注入 LangGraph state 的 ``_trace_context`` key，
    让子节点的 ``@trace_node`` 自动建立层级关系。

    未启用 Langfuse 时返回空 dict（不抛异常）。

    S9 修复（GLM 2026-06-18 review）：langfuse v3 移除了 ``client.trace()``，本函数
    在 v3 下走降级分支：返回空 dict，且 logger.error 提示"trace 层级未建立"。
    这意味着生产（langfuse>=3.0.0）下 P4 的"一条请求一条 trace 下挂多个 span"层级
    关系**实际未建立**——子节点会退化为独立 span。完整迁移见 ADR-0012（待补）。
    """
    from observability.langfuse_client import get_langfuse_client

    client = get_langfuse_client()
    if client is None:
        return {}

    # langfuse v3 兼容：v3 移除了 client.trace()，改用 context-based API。
    # 未迁移到 v3 API 前，trace 不可用时优雅降级（不阻塞业务）。
    # 详见 ADR-0012（待补）：langfuse v2→v3 迁移。
    if not hasattr(client, "trace"):
        # S9：用 logger.error 让生产告警更显眼（之前 logger.warning 容易被淹没）
        # 降级标志：v3_degraded=True，方便测试断言
        logger.error(
            "langfuse_v3_trace_degraded",
            reason="client.trace() missing in langfuse v3; trace hierarchy NOT established",
            impact="子 span 退化为独立 span，parent/child 关系丢失（ADR-0012 待补迁移）",
        )
        return {"_v3_degraded": True}

    trace_meta: dict[str, str] = {}
    if session_id:
        trace_meta["session_id"] = session_id
    if user_id:
        trace_meta["user_id"] = user_id
    if metadata:
        trace_meta.update(metadata)

    trace_obj = client.trace(
        name=name,
        metadata=trace_meta if trace_meta else None,
    )
    return {
        "trace_id": trace_obj.trace_id,
    }


def trace_node(  # noqa: C901
    name: str | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """装饰器：把函数包装为 Langfuse span。

    :param name: span 名称，默认用 ``module.qualname``

    若第一个参数是 dict 且含 ``_trace_context``，则在其 trace 下创建子 span；
    否则退化为独立 span（向后兼容）。
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
                ctx = _get_trace_context(args, kwargs)

                if ctx and isinstance(ctx, dict) and "trace_id" in ctx and hasattr(client, "trace"):
                    trace_obj = client.trace(id=ctx["trace_id"])
                    parent_context = trace_obj
                    with parent_context.span(name=span_name) as span:
                        for k, v in meta.items():
                            span.update(metadata={k: v})
                        try:
                            result = await func(*args, **kwargs)
                        except Exception as exc:
                            span.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")
                            raise
                        return result  # type: ignore[no-any-return]
                else:
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
            ctx = _get_trace_context(args, kwargs)

            if ctx and isinstance(ctx, dict) and "trace_id" in ctx and hasattr(client, "trace"):
                trace_obj = client.trace(id=ctx["trace_id"])
                parent_context = trace_obj
                with parent_context.span(name=span_name) as span:
                    for k, v in meta.items():
                        span.update(metadata={k: v})
                    try:
                        result = func(*args, **kwargs)
                    except Exception as exc:
                        span.update(level="ERROR", status_message=f"{type(exc).__name__}: {exc}")
                        raise
                    return result
            else:
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
