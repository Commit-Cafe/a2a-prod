"""A2A JSON-RPC 客户端。

Orchestrator 用它调用下游 Agent 的 ``message/send`` 端点。

设计原则：
- **协议优先**：始终走 A2A JSON-RPC，不绕过协议直接 import（SPEC §1）
- **复用 httpx.AsyncClient**：模块级共享，避免每次新建连接（CODESTYLE §4.3）
- **错误分层**：基础设施层异常 → 业务层 ``A2AClientError``（SPEC §1.5）

参考：
- A2A JSON-RPC spec：https://a2a-protocol.org/latest/spec/
- a2a-sdk 0.3.x ``message/send`` schema：见 tests/test_p1_e2e.py
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog

from observability.tracing import trace_node

logger = structlog.get_logger(__name__)


# ============================================================
# 异常（SPEC §1.5 业务层）
# ============================================================


class A2AClientError(Exception):
    """A2A 客户端业务层异常基类。"""


class A2ATimeoutError(A2AClientError):
    """下游 Agent 响应超时。"""


class A2AProtocolError(A2AClientError):
    """下游 Agent 返回了不符合 A2A 协议的响应（JSON-RPC error 或缺 result）。"""


class A2AHTTPError(A2AClientError):
    """下游 Agent HTTP 层错误（非 200）。"""


# ============================================================
# 客户端
# ============================================================


# 模块级共享 httpx.AsyncClient（CODESTYLE §4.3）
# 重试 / keepalive 由 httpx 自动管理；P4 阶段 Langfuse 注入 trace 时统一改造
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """惰性创建模块级 httpx.AsyncClient。"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=120.0, write=5.0, pool=10.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close_client() -> None:
    """FastAPI shutdown 钩子调用，关闭 httpx 连接池。"""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ============================================================
# A2A message/send
# ============================================================


@trace_node(name="orchestrator.a2a.message_send")
async def message_send(
    agent_base_url: str,
    text: str,
    *,
    request_id: int = 1,
    session_id: str | None = None,
    user_id: str | None = None,
) -> str:
    """调用下游 Agent 的 ``message/send``，返回 Agent 输出的文本。

    Args:
        agent_base_url: 下游 Agent 基础 URL，如 ``http://localhost:12001``
        text: 用户 prompt
        request_id: JSON-RPC request id（默认 1，e2e 用）
        session_id: 透传到 Langfuse trace metadata（SPEC §3.8.4）
        user_id: 透传到 Langfuse trace metadata

    Returns:
        Agent 输出的文本（从 task.artifacts[0].parts[*].text 提取）

    Raises:
        A2ATimeoutError: Agent 响应超时
        A2AHTTPError: HTTP 非 200
        A2AProtocolError: JSON-RPC error / 缺 result / 解析失败
    """
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
                "messageId": f"orch-{uuid.uuid4().hex[:12]}",
                "kind": "message",
            }
        },
        "id": request_id,
    }

    log = logger.bind(agent_url=agent_base_url, request_id=request_id, session_id=session_id or "-")
    await log.ainfo("a2a_send_start", text_preview=text[:60])

    client = _get_client()
    try:
        response = await client.post(agent_base_url, json=payload)
    except httpx.TimeoutException as e:
        await log.aerror("a2a_send_timeout", error=str(e))
        raise A2ATimeoutError(f"timeout calling {agent_base_url}: {e}") from e
    except httpx.HTTPError as e:
        await log.aerror("a2a_send_http_error", error=str(e))
        raise A2AHTTPError(f"http error calling {agent_base_url}: {e}") from e

    if response.status_code != 200:
        await log.aerror(
            "a2a_send_non_200",
            status=response.status_code,
            body_preview=response.text[:200],
        )
        raise A2AHTTPError(
            f"{agent_base_url} returned HTTP {response.status_code}: {response.text[:200]}"
        )

    body = response.json()
    if "error" in body:
        await log.aerror("a2a_send_jsonrpc_error", error=body["error"])
        raise A2AProtocolError(f"JSON-RPC error from {agent_base_url}: {body['error']}")

    if "result" not in body:
        raise A2AProtocolError(f"missing 'result' in response from {agent_base_url}: {body}")

    return _extract_text_from_task(body["result"])


def _extract_text_from_task(task: dict[str, Any]) -> str:
    """从 A2A Task 对象提取文本。

    Task 结构（a2a-sdk 0.3.x）：
        {
            "kind": "task",
            "status": {"state": "completed", ...},
            "artifacts": [
                {
                    "parts": [
                        {"kind": "text", "text": "..."},
                        ...
                    ]
                }
            ]
        }
    """
    parts: list[str] = []

    # 优先从 artifacts 提取（A2A 规范位置）
    for artifact in task.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            if part.get("kind") == "text" and part.get("text"):
                parts.append(part["text"])

    # 兜底：从 status.message 提取（部分 Agent 把结果放这里）
    if not parts:
        status = task.get("status") or {}
        message = status.get("message") or {}
        for part in message.get("parts") or []:
            if part.get("kind") == "text" and part.get("text"):
                parts.append(part["text"])

    if not parts:
        raise A2AProtocolError(
            f"no text part found in task: artifacts={task.get('artifacts')!r}, "
            f"status={task.get('status')!r}"
        )

    return "\n\n".join(parts)
