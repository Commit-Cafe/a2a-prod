"""A2A 客户端契约测试（SPEC §P2 A2A JSON-RPC）。

校验内容：
1. 成功路径：从 ``task.artifacts[].parts[]`` 提取文本
2. 兜底路径：从 ``task.status.message.parts[]`` 提取
3. 异常分层：timeout / HTTP / JSON-RPC / 协议缺失
4. 多 part 拼接

Mock 策略：
- ``unittest.mock.AsyncMock`` mock ``httpx.AsyncClient.post``
- 不引入 respx（避免新依赖）
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from orchestrator import a2a_client
from orchestrator.a2a_client import (
    A2AClientError,
    A2AHTTPError,
    A2AProtocolError,
    A2ATimeoutError,
    _extract_text_from_task,
    message_send,
)

# ============================================================
# 辅助：构造 A2A Task 响应
# ============================================================


def _task_with_artifacts(texts: list[str]) -> dict[str, Any]:
    """构造 artifacts.parts 含多段 text 的 task。"""
    return {
        "kind": "task",
        "status": {"state": "completed"},
        "artifacts": [
            {
                "parts": [{"kind": "text", "text": t} for t in texts],
            }
        ],
    }


def _task_with_status_message(text: str) -> dict[str, Any]:
    """构造结果放在 status.message.parts 的 task（兜底路径）。"""
    return {
        "kind": "task",
        "status": {
            "state": "completed",
            "message": {
                "role": "agent",
                "parts": [{"kind": "text", "text": text}],
            },
        },
    }


def _jsonrpc_success(result: dict[str, Any]) -> dict[str, Any]:
    """构造 JSON-RPC 成功响应。"""
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def _jsonrpc_error(code: int, message: str) -> dict[str, Any]:
    """构造 JSON-RPC 错误响应。"""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": code, "message": message},
    }


def _make_response(
    status_code: int = 200,
    json_body: dict[str, Any] | None = None,
) -> MagicMock:
    """构造 mock httpx.Response。"""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    resp.text = str(json_body or {})
    return resp


@pytest.fixture(autouse=True)
def _reset_client() -> Iterator[None]:
    """每个测试前重置模块级 httpx client，避免状态泄漏。"""
    a2a_client._client = None
    yield
    a2a_client._client = None


# ============================================================
# Test 1：_extract_text_from_task 纯函数
# ============================================================


class TestExtractText:
    """``_extract_text_from_task`` 文本提取。"""

    def test_single_artifact_text(self) -> None:
        task = _task_with_artifacts(["hello"])
        assert _extract_text_from_task(task) == "hello"

    def test_multiple_parts_joined_by_double_newline(self) -> None:
        task = _task_with_artifacts(["first", "second", "third"])
        assert _extract_text_from_task(task) == "first\n\nsecond\n\nthird"

    def test_status_message_fallback(self) -> None:
        """无 artifacts 时从 status.message 提取。"""
        task = _task_with_status_message("from status")
        assert _extract_text_from_task(task) == "from status"

    def test_artifacts_takes_precedence_over_status(self) -> None:
        """同时存在时，artifacts 优先。"""
        task = {
            "kind": "task",
            "status": {
                "state": "completed",
                "message": {
                    "role": "agent",
                    "parts": [{"kind": "text", "text": "from status"}],
                },
            },
            "artifacts": [
                {"parts": [{"kind": "text", "text": "from artifacts"}]},
            ],
        }
        assert _extract_text_from_task(task) == "from artifacts"

    def test_no_text_anywhere_raises(self) -> None:
        """无任何 text part 时抛 A2AProtocolError。"""
        task: dict[str, Any] = {
            "kind": "task",
            "status": {"state": "completed"},
            "artifacts": [{"parts": [{"kind": "data", "data": "binary"}]}],
        }
        with pytest.raises(A2AProtocolError, match="no text part"):
            _extract_text_from_task(task)

    def test_empty_artifacts_falls_back_to_status(self) -> None:
        """artifacts=[] 视为无，从 status.message 取。"""
        task: dict[str, Any] = {
            "kind": "task",
            "status": {
                "state": "completed",
                "message": {
                    "role": "agent",
                    "parts": [{"kind": "text", "text": "fallback"}],
                },
            },
            "artifacts": [],
        }
        assert _extract_text_from_task(task) == "fallback"

    def test_skips_non_text_parts(self) -> None:
        """``kind != 'text'`` 的 part 被忽略。"""
        task: dict[str, Any] = {
            "kind": "task",
            "status": {"state": "completed"},
            "artifacts": [
                {
                    "parts": [
                        {"kind": "data", "data": "..."},
                        {"kind": "text", "text": "real"},
                        {"kind": "text", "text": "answer"},
                    ]
                }
            ],
        }
        assert _extract_text_from_task(task) == "real\n\nanswer"


# ============================================================
# Test 2：message_send 成功路径
# ============================================================


class TestMessageSendSuccess:
    """``message_send`` HTTP 成功路径。"""

    async def test_returns_text_from_artifact(self) -> None:
        body = _jsonrpc_success(_task_with_artifacts(["hello from agent"]))
        mock_resp = _make_response(200, body)

        with patch.object(a2a_client, "_get_client") as mock_get:
            mock_client = MagicMock(spec=httpx.AsyncClient)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_client

            text = await message_send("http://test:8000", "ping")

        assert text == "hello from agent"
        mock_client.post.assert_awaited_once()
        # 校验 payload 结构
        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["jsonrpc"] == "2.0"
        assert payload["method"] == "message/send"
        assert payload["params"]["message"]["parts"][0]["text"] == "ping"

    async def test_returns_text_from_status_message(self) -> None:
        body = _jsonrpc_success(_task_with_status_message("status reply"))
        mock_resp = _make_response(200, body)

        with patch.object(a2a_client, "_get_client") as mock_get:
            mock_client = MagicMock(spec=httpx.AsyncClient)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_client

            text = await message_send("http://test:8000", "ping")

        assert text == "status reply"

    async def test_message_id_is_unique(self) -> None:
        """每次调用生成不同的 messageId。"""
        body = _jsonrpc_success(_task_with_artifacts(["ok"]))
        mock_resp = _make_response(200, body)

        ids: list[str] = []
        with patch.object(a2a_client, "_get_client") as mock_get:
            mock_client = MagicMock(spec=httpx.AsyncClient)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_client

            await message_send("http://test:8000", "q1")
            ids.append(mock_client.post.call_args.kwargs["json"]["params"]["message"]["messageId"])
            await message_send("http://test:8000", "q2")
            ids.append(mock_client.post.call_args.kwargs["json"]["params"]["message"]["messageId"])

        assert len(set(ids)) == 2
        assert all(mid.startswith("orch-") for mid in ids)


# ============================================================
# Test 3：message_send 异常分层
# ============================================================


class TestMessageSendErrors:
    """异常分层：超时 / HTTP / JSON-RPC / 协议缺失。"""

    async def test_timeout_raises_a2atimeouterror(self) -> None:
        with patch.object(a2a_client, "_get_client") as mock_get:
            mock_client = MagicMock(spec=httpx.AsyncClient)
            mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("read timeout"))
            mock_get.return_value = mock_client

            with pytest.raises(A2ATimeoutError, match="timeout"):
                await message_send("http://test:8000", "ping")

    async def test_generic_http_error_raises_a2ahttperror(self) -> None:
        with patch.object(a2a_client, "_get_client") as mock_get:
            mock_client = MagicMock(spec=httpx.AsyncClient)
            mock_client.post = AsyncMock(side_effect=httpx.HTTPError("connection reset"))
            mock_get.return_value = mock_client

            with pytest.raises(A2AHTTPError, match="http error"):
                await message_send("http://test:8000", "ping")

    async def test_non_200_raises_a2ahttperror(self) -> None:
        mock_resp = _make_response(500, {"error": "internal"})

        with patch.object(a2a_client, "_get_client") as mock_get:
            mock_client = MagicMock(spec=httpx.AsyncClient)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_client

            with pytest.raises(A2AHTTPError, match="HTTP 500"):
                await message_send("http://test:8000", "ping")

    async def test_404_raises_a2ahttperror(self) -> None:
        mock_resp = _make_response(404, {})

        with patch.object(a2a_client, "_get_client") as mock_get:
            mock_client = MagicMock(spec=httpx.AsyncClient)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_client

            with pytest.raises(A2AHTTPError, match="HTTP 404"):
                await message_send("http://test:8000", "ping")

    async def test_jsonrpc_error_raises_a2aprotocolerror(self) -> None:
        body = _jsonrpc_error(-32601, "Method not found")
        mock_resp = _make_response(200, body)

        with patch.object(a2a_client, "_get_client") as mock_get:
            mock_client = MagicMock(spec=httpx.AsyncClient)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_client

            with pytest.raises(A2AProtocolError, match="JSON-RPC error"):
                await message_send("http://test:8000", "ping")

    async def test_missing_result_raises_a2aprotocolerror(self) -> None:
        body = {"jsonrpc": "2.0", "id": 1}  # 缺 result 也缺 error
        mock_resp = _make_response(200, body)

        with patch.object(a2a_client, "_get_client") as mock_get:
            mock_client = MagicMock(spec=httpx.AsyncClient)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_client

            with pytest.raises(A2AProtocolError, match="missing 'result'"):
                await message_send("http://test:8000", "ping")

    async def test_no_text_in_task_raises_a2aprotocolerror(self) -> None:
        body = _jsonrpc_success({"kind": "task", "status": {"state": "completed"}})
        mock_resp = _make_response(200, body)

        with patch.object(a2a_client, "_get_client") as mock_get:
            mock_client = MagicMock(spec=httpx.AsyncClient)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_get.return_value = mock_client

            with pytest.raises(A2AProtocolError, match="no text part"):
                await message_send("http://test:8000", "ping")


# ============================================================
# Test 4：异常继承关系
# ============================================================


class TestExceptionHierarchy:
    """SPEC §1.5 业务层异常统一继承 A2AClientError。"""

    def test_all_subclass_of_a2aclienterror(self) -> None:
        assert issubclass(A2ATimeoutError, A2AClientError)
        assert issubclass(A2AHTTPError, A2AClientError)
        assert issubclass(A2AProtocolError, A2AClientError)

    def test_a2aclienterror_is_exception(self) -> None:
        assert issubclass(A2AClientError, Exception)
