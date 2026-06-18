"""Langfuse 客户端封装契约测试（SPEC §3.8）。

守护点：
- is_langfuse_enabled() 在 PK/SK/HOST 任一缺失时返回 False
- setup_otlp_env() 设置的 OTEL env 变量格式正确（防御 issue #9871 401）
  - endpoint = ${LANGFUSE_HOST}/api/public/otel
  - headers = Authorization=Basic ${base64(PK:SK)}
- get_langfuse_client() 未启用时返回 None
- health_check() 未启用 / SDK 异常时返回 False（graceful degradation）
- trace_node 装饰器在未启用 Langfuse 时是 no-op（不抛异常、返回原值）
- trace_node 装饰器在启用时调 SDK，metadata 从 kwargs/state 中正确提取

不依赖 docker；用 monkeypatch 控制 env。
"""

from __future__ import annotations

import base64
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from observability.langfuse_client import (
    get_langfuse_client,
    health_check,
    is_langfuse_enabled,
    setup_otlp_env,
)
from observability.tracing import start_trace, trace_node

# ============================================================
# 公共 fixture
# ============================================================


@pytest.fixture
def enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 LANGFUSE_* 三个 env 都设上。"""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test-123")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test-456")
    monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse-web:3000")


@pytest.fixture
def disabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清空所有 LANGFUSE_* env。"""
    for key in (
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
    ):
        monkeypatch.delenv(key, raising=False)


# ============================================================
# is_langfuse_enabled
# ============================================================


class TestIsEnabled:
    def test_enabled_when_all_three_set(self, enabled_env: None) -> None:
        assert is_langfuse_enabled() is True

    def test_disabled_when_no_env(self, disabled_env: None) -> None:
        assert is_langfuse_enabled() is False

    @pytest.mark.parametrize(
        "missing_key",
        ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"],
    )
    def test_disabled_when_any_missing(
        self, enabled_env: None, missing_key: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(missing_key, raising=False)
        assert is_langfuse_enabled() is False

    def test_disabled_when_value_empty_string(
        self, enabled_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
        assert is_langfuse_enabled() is False


# ============================================================
# setup_otlp_env
# ============================================================


class TestSetupOtlpEnv:
    def test_sets_endpoint(self, enabled_env: None) -> None:
        setup_otlp_env()
        assert (
            os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://langfuse-web:3000/api/public/otel"
        )

    def test_sets_auth_header_with_basic_token(self, enabled_env: None) -> None:
        setup_otlp_env()
        headers = os.environ["OTEL_EXPORTER_OTLP_HEADERS"]
        # Authorization=Basic <base64(pk:sk)>
        expected_token = base64.b64encode(b"pk-test-123:sk-test-456").decode("ascii")
        assert headers == f"Authorization=Basic {expected_token}"

    def test_strips_trailing_slash_from_host(
        self, enabled_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LANGFUSE_HOST", "http://langfuse-web:3000/")
        setup_otlp_env()
        assert (
            os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://langfuse-web:3000/api/public/otel"
        )

    def test_idempotent(self, enabled_env: None) -> None:
        setup_otlp_env()
        first_endpoint = os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"]
        setup_otlp_env()
        assert os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] == first_endpoint

    def test_noop_when_disabled(self, disabled_env: None) -> None:
        # 即使没启用，也不抛异常
        setup_otlp_env()
        assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ
        assert "OTEL_EXPORTER_OTLP_HEADERS" not in os.environ


# ============================================================
# get_langfuse_client
# ============================================================


class TestGetClient:
    def test_returns_none_when_disabled(self, disabled_env: None) -> None:
        assert get_langfuse_client() is None

    def test_returns_client_when_enabled(self, enabled_env: None) -> None:
        # 不实际调 SDK 的真实逻辑，只验证返回非 None
        with patch("langfuse.get_client") as mock_sdk_get_client:
            mock_sdk_get_client.return_value = MagicMock(name="fake_langfuse_client")
            client = get_langfuse_client()
        assert client is not None
        assert client is mock_sdk_get_client.return_value
        mock_sdk_get_client.assert_called_once()


# ============================================================
# health_check
# ============================================================


class TestHealthCheck:
    def test_returns_false_when_disabled(self, disabled_env: None) -> None:
        assert health_check() is False

    def test_returns_true_when_auth_check_passes(self, enabled_env: None) -> None:
        with patch("langfuse.get_client") as mock_sdk_get_client:
            fake_client = MagicMock()
            fake_client.auth_check = MagicMock(return_value=None)
            mock_sdk_get_client.return_value = fake_client
            assert health_check() is True
            fake_client.auth_check.assert_called_once()

    def test_returns_false_when_auth_check_raises(self, enabled_env: None) -> None:
        with patch("langfuse.get_client") as mock_sdk_get_client:
            fake_client = MagicMock()
            fake_client.auth_check = MagicMock(side_effect=RuntimeError("network error"))
            mock_sdk_get_client.return_value = fake_client
            assert health_check() is False


# ============================================================
# trace_node 装饰器
# ============================================================


class TestTraceNodeDecorator:
    def test_noop_when_langfuse_disabled(self, disabled_env: None) -> None:
        @trace_node(name="test.span")
        def sync_func(x: int) -> int:
            return x * 2

        assert sync_func(5) == 10

    async def test_async_noop_when_disabled(self, disabled_env: None) -> None:
        @trace_node(name="test.async_span")
        async def async_func(x: int) -> int:
            return x * 3

        assert await async_func(4) == 12

    def test_preserves_function_metadata(self, disabled_env: None) -> None:
        @trace_node(name="test.meta")
        def my_func() -> str:
            """docstring."""
            return "ok"

        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "docstring."

    def test_invoke_span_when_enabled(self, enabled_env: None) -> None:
        # 模拟一个 Langfuse client + 上下文管理器
        fake_client = MagicMock()
        fake_span = MagicMock()
        # start_as_current_observation 返回的是 contextmanager
        fake_client.start_as_current_observation = MagicMock()
        fake_client.start_as_current_observation.return_value.__enter__ = MagicMock(
            return_value=fake_span
        )
        fake_client.start_as_current_observation.return_value.__exit__ = MagicMock(
            return_value=False
        )

        with patch("observability.langfuse_client.get_langfuse_client", return_value=fake_client):

            @trace_node(name="test.invoke")
            def my_func(x: int) -> int:
                return x + 1

            result = my_func(10)

        assert result == 11
        # 验证 span name 传对了
        call_kwargs = fake_client.start_as_current_observation.call_args.kwargs
        assert call_kwargs["name"] == "test.invoke"
        assert call_kwargs["as_type"] == "span"

    def test_extract_metadata_from_kwargs(self, enabled_env: None) -> None:
        """metadata 从 kwargs 提取：request_id / session_id / user_id。"""
        fake_client = MagicMock()
        fake_span = MagicMock()
        fake_client.start_as_current_observation = MagicMock()
        fake_client.start_as_current_observation.return_value.__enter__ = MagicMock(
            return_value=fake_span
        )
        fake_client.start_as_current_observation.return_value.__exit__ = MagicMock(
            return_value=False
        )

        with patch("observability.langfuse_client.get_langfuse_client", return_value=fake_client):

            @trace_node(name="test.kwargs")
            def my_func(state: dict[str, Any], request_id: str) -> str:
                return state.get("foo", "") + request_id  # type: ignore[no-any-return]

            result = my_func({"foo": "bar"}, request_id="req-001")

        assert result == "barreq-001"
        # 验证 span.update(metadata=...) 被以 kwargs 方式调
        metadata_call: dict[str, Any] = {}
        for call in fake_span.update.call_args_list:
            if "metadata" in call.kwargs:
                metadata_call.update(call.kwargs["metadata"])
        assert metadata_call.get("request_id") == "req-001"

    def test_extract_metadata_from_state_dict(self, enabled_env: None) -> None:
        """metadata 从 state dict（LangGraph 节点场景）提取。"""
        fake_client = MagicMock()
        fake_span = MagicMock()
        fake_client.start_as_current_observation = MagicMock()
        fake_client.start_as_current_observation.return_value.__enter__ = MagicMock(
            return_value=fake_span
        )
        fake_client.start_as_current_observation.return_value.__exit__ = MagicMock(
            return_value=False
        )

        with patch("observability.langfuse_client.get_langfuse_client", return_value=fake_client):

            @trace_node(name="test.state")
            def langgraph_node(state: dict[str, Any]) -> dict[str, Any]:
                return {"ok": True}

            result = langgraph_node(
                {
                    "user_query": "hi",
                    "session_id": "sess-42",
                    "user_id": "alice",
                }
            )

        assert result == {"ok": True}
        metadata_call: dict[str, Any] = {}
        for call in fake_span.update.call_args_list:
            if "metadata" in call.kwargs:
                metadata_call.update(call.kwargs["metadata"])
        assert metadata_call.get("session_id") == "sess-42"
        assert metadata_call.get("user_id") == "alice"

    def test_exception_marks_span_error(self, enabled_env: None) -> None:
        """函数抛异常时 span 标记为 ERROR 并 reraise。"""
        fake_client = MagicMock()
        fake_span = MagicMock()
        fake_client.start_as_current_observation = MagicMock()
        fake_client.start_as_current_observation.return_value.__enter__ = MagicMock(
            return_value=fake_span
        )
        fake_client.start_as_current_observation.return_value.__exit__ = MagicMock(
            return_value=False
        )

        with patch("observability.langfuse_client.get_langfuse_client", return_value=fake_client):

            @trace_node(name="test.error")
            def fail() -> None:
                raise ValueError("boom")

            with pytest.raises(ValueError, match="boom"):
                fail()

        # 至少有一次 update 调 level=ERROR
        error_calls = [
            call for call in fake_span.update.call_args_list if call.kwargs.get("level") == "ERROR"
        ]
        assert error_calls, f"expected ERROR update, got: {fake_span.update.call_args_list}"


# ============================================================
# start_trace() 函数
# ============================================================


class TestStartTrace:
    def test_returns_empty_dict_when_disabled(self, disabled_env: None) -> None:
        ctx = start_trace("test.trace")
        assert ctx == {}

    def test_returns_trace_id_when_enabled(self, enabled_env: None) -> None:
        fake_client = MagicMock()
        fake_trace = MagicMock()
        fake_trace.trace_id = "trace-abc-123"
        fake_client.trace = MagicMock(return_value=fake_trace)

        with patch("observability.langfuse_client.get_langfuse_client", return_value=fake_client):
            ctx = start_trace(
                "orchestrator.orchestrate",
                session_id="sess-1",
                user_id="alice",
            )

        assert "trace_id" in ctx
        assert ctx["trace_id"] == "trace-abc-123"
        fake_client.trace.assert_called_once()
        trace_kwargs = fake_client.trace.call_args.kwargs
        assert trace_kwargs["name"] == "orchestrator.orchestrate"
        assert trace_kwargs["metadata"]["session_id"] == "sess-1"
        assert trace_kwargs["metadata"]["user_id"] == "alice"


# ============================================================
# trace_node with _trace_context (parent-child)
# ============================================================


class TestTraceNodeWithContext:
    def test_uses_parent_trace_when_context_present(self, enabled_env: None) -> None:
        fake_client = MagicMock()
        fake_trace = MagicMock()
        fake_span = MagicMock()
        fake_trace.span = MagicMock()
        fake_trace.span.return_value.__enter__ = MagicMock(return_value=fake_span)
        fake_trace.span.return_value.__exit__ = MagicMock(return_value=False)
        fake_client.trace = MagicMock(return_value=fake_trace)

        with patch("observability.langfuse_client.get_langfuse_client", return_value=fake_client):

            @trace_node(name="test.child_span")
            def node_func(state: dict[str, Any]) -> dict[str, Any]:
                return {"done": True}

            result = node_func(
                {
                    "user_query": "hi",
                    "_trace_context": {"trace_id": "trace-xyz-789"},
                }
            )

        assert result == {"done": True}
        fake_client.trace.assert_called_once_with(id="trace-xyz-789")
        fake_trace.span.assert_called_once_with(name="test.child_span")

    async def test_async_uses_parent_trace(self, enabled_env: None) -> None:
        fake_client = MagicMock()
        fake_trace = MagicMock()
        fake_span = MagicMock()
        fake_trace.span = MagicMock()
        fake_trace.span.return_value.__enter__ = MagicMock(return_value=fake_span)
        fake_trace.span.return_value.__exit__ = MagicMock(return_value=False)
        fake_client.trace = MagicMock(return_value=fake_trace)

        with patch("observability.langfuse_client.get_langfuse_client", return_value=fake_client):

            @trace_node(name="test.async_child")
            async def async_node(state: dict[str, Any]) -> dict[str, Any]:
                return {"async": True}

            result = await async_node(
                {
                    "_trace_context": {"trace_id": "trace-async-001"},
                }
            )

        assert result == {"async": True}
        fake_client.trace.assert_called_once_with(id="trace-async-001")

    def test_falls_back_to_standalone_span_without_context(self, enabled_env: None) -> None:
        fake_client = MagicMock()
        fake_span = MagicMock()
        fake_client.start_as_current_observation = MagicMock()
        fake_client.start_as_current_observation.return_value.__enter__ = MagicMock(
            return_value=fake_span
        )
        fake_client.start_as_current_observation.return_value.__exit__ = MagicMock(
            return_value=False
        )

        with patch("observability.langfuse_client.get_langfuse_client", return_value=fake_client):

            @trace_node(name="test.standalone")
            def node_func(state: dict[str, Any]) -> dict[str, Any]:
                return {"ok": True}

            result = node_func({"user_query": "hi"})

        assert result == {"ok": True}
        fake_client.start_as_current_observation.assert_called_once()
