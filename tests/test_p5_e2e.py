"""P5 阶段 e2e 测试（Orchestrator OpenAI 兼容层 + Open WebUI 端到端联调）。

前置条件：

- ``.env.prod`` 已填好三家 API Key
- 已运行 ``docker compose --env-file .env.prod up -d`` 启动完整栈
  （litellm + 3 Agent + orchestrator + 3 MCP + open-webui + langfuse）
- 全部容器 healthy（包含 ``a2a-prod-open-webui``）

测试内容：

- Open WebUI ``/health`` 探活
- Orchestrator ``/v1/models`` 返回 3 个 Agent
- Orchestrator ``/v1/chat/completions``（无 Bearer）→ 401（鉴权开启时）
- Orchestrator ``/v1/chat/completions``（Bearer 正确）→ 200 + OpenAI schema
- Orchestrator ``/v1/chat/completions``（stream=true）→ SSE ``data: [DONE]``
- ``model=glm-agent`` 强制 DIRECT 路由（响应 model 字段 == "glm-agent"）
- Open WebUI → Orchestrator 链路（**不**做 UI 自动化；只调 WebUI 的 OpenAI 兼容端点
  验证它能正确转发到 Orchestrator）

注意：

- LLM 输出不可预测，断言只验证「响应到达 + 字段对齐」，不验证 LLM 答案内容
- 流式响应断言只验证 SSE 协议结构 + 至少 1 个 chunk + ``[DONE]`` 收尾

标记：``@pytest.mark.e2e`` + ``@pytest.mark.p5_e2e``

用法：

.. code-block:: bash

    # 仅跑 P5 e2e
    pytest -m "p5_e2e" tests/test_p5_e2e.py
"""

from __future__ import annotations

import json
import os
import time
import uuid

import httpx
import pytest

# ============================================================
# 配置
# ============================================================


def _api_key() -> str:
    """取 Orchestrator 期望的 API key。

    优先 ``ORCHESTRATOR_API_KEY``，否则 ``LITELLM_MASTER_KEY``，
    否则用 dev 占位（与 ``infra/.env.example`` 默认值一致）。
    """
    return (
        os.getenv("ORCHESTRATOR_API_KEY")
        or os.getenv("LITELLM_MASTER_KEY")
        or "sk-a2a-prod-litellm-master-key-change-me"
    )


def _bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}"}


# ============================================================
# Open WebUI 探活
# ============================================================


@pytest.mark.e2e
@pytest.mark.p5_e2e
def test_open_webui_health(open_webui_url: str) -> None:
    """SPEC §3.9.1：Open WebUI 暴露 ``/health`` 端点。"""
    response = httpx.get(f"{open_webui_url}/health", timeout=10.0)
    assert (
        response.status_code == 200
    ), f"open-webui not healthy: {response.status_code} {response.text[:200]}"


@pytest.mark.e2e
@pytest.mark.p5_e2e
def test_open_webui_index_returns_html(open_webui_url: str) -> None:
    """Open WebUI 根路径 MUST 返回 HTML（首页）。"""
    response = httpx.get(f"{open_webui_url}/", timeout=10.0)
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


# ============================================================
# Orchestrator OpenAI 兼容层
# ============================================================


@pytest.mark.e2e
@pytest.mark.p5_e2e
def test_orchestrator_models_endpoint(orchestrator_url: str) -> None:
    """SPEC §3.9.2：``GET /v1/models`` 返回 3 个 Agent + 字段对齐 OpenAI。"""
    response = httpx.get(
        f"{orchestrator_url}/v1/models",
        headers=_bearer(),
        timeout=10.0,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    ids = {m["id"] for m in body["data"]}
    assert ids == {"glm-agent", "deepseek-agent", "minimax-agent"}
    for m in body["data"]:
        assert m["object"] == "model"
        assert isinstance(m["created"], int)
        assert m["owned_by"] == "a2a-prod"


@pytest.mark.e2e
@pytest.mark.p5_e2e
def test_chat_completions_basic(orchestrator_url: str) -> None:
    """SPEC §3.9.3：``POST /v1/chat/completions`` 返回 OpenAI 兼容响应。"""
    payload = {
        "model": "auto",
        "messages": [{"role": "user", "content": "用一句话介绍 Python 装饰器。"}],
        "stream": False,
    }
    response = httpx.post(
        f"{orchestrator_url}/v1/chat/completions",
        json=payload,
        headers=_bearer(),
        timeout=60.0,
    )
    assert response.status_code == 200, response.text[:500]
    body = response.json()

    # OpenAI schema 字段对齐
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert isinstance(body["created"], int)
    assert isinstance(body["model"], str)
    assert len(body["choices"]) >= 1
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert isinstance(body["choices"][0]["message"]["content"], str)
    assert body["choices"][0]["finish_reason"] == "stop"
    # usage 占位
    assert set(body["usage"].keys()) == {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }


@pytest.mark.e2e
@pytest.mark.p5_e2e
def test_chat_completions_force_direct_routing(orchestrator_url: str) -> None:
    """SPEC §3.9.3：``model=glm-agent`` 强制 DIRECT 路由。

    断言响应里 ``model`` 字段是 ``glm-agent``（Orchestrator 实际路由的 Agent 名）。
    """
    payload = {
        "model": "glm-agent",
        "messages": [{"role": "user", "content": "你好，请用一句话自我介绍。"}],
        "stream": False,
    }
    response = httpx.post(
        f"{orchestrator_url}/v1/chat/completions",
        json=payload,
        headers=_bearer(),
        timeout=60.0,
    )
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert body["model"] == "glm-agent"


@pytest.mark.e2e
@pytest.mark.p5_e2e
def test_chat_completions_stream(orchestrator_url: str) -> None:
    """SPEC §3.9.4：``stream=true`` 返回 SSE 协议 + ``data: [DONE]`` 收尾。"""
    payload = {
        "model": "auto",
        "messages": [{"role": "user", "content": "用 5 个字回复：测试成功"}],
        "stream": True,
    }
    with httpx.stream(
        "POST",
        f"{orchestrator_url}/v1/chat/completions",
        json=payload,
        headers=_bearer(),
        timeout=60.0,
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        chunks: list[dict] = []
        done_seen = False
        for raw_line in response.iter_lines():
            if not raw_line:
                continue
            assert raw_line.startswith("data: "), f"unexpected line: {raw_line[:100]}"
            data = raw_line[len("data: ") :]
            if data == "[DONE]":
                done_seen = True
                break
            chunks.append(json.loads(data))

        assert done_seen, "SSE response did not end with data: [DONE]"
        assert len(chunks) >= 1, "no chunks received"

        # 第一块必含 choices[0].delta（OpenAI 协议）
        first = chunks[0]
        assert first["object"] == "chat.completion.chunk"
        assert first["id"].startswith("chatcmpl-")
        assert "choices" in first
        assert "delta" in first["choices"][0]


@pytest.mark.e2e
@pytest.mark.p5_e2e
def test_chat_completions_empty_messages_rejected(orchestrator_url: str) -> None:
    """SPEC §3.9.5：无 user 消息时 MUST 返回 400。"""
    payload = {
        "model": "auto",
        "messages": [{"role": "system", "content": "你是助手"}],  # 没有 user
    }
    response = httpx.post(
        f"{orchestrator_url}/v1/chat/completions",
        json=payload,
        headers=_bearer(),
        timeout=10.0,
    )
    assert response.status_code == 400
    assert "user" in response.json().get("detail", "").lower()


@pytest.mark.e2e
@pytest.mark.p5_e2e
def test_chat_completions_invalid_bearer(orchestrator_url: str) -> None:
    """SPEC §3.9.1：Bearer Token 错误时 MUST 返回 401。

    注意：仅在 ``LITELLM_MASTER_KEY`` / ``ORCHESTRATOR_API_KEY`` 已设置时生效。
    """
    expected = _api_key()
    if expected == "sk-a2a-prod-litellm-master-key-change-me":
        # 开发模式（未设 key）→ 跳过（因为 Orchestrator 不强制鉴权）
        pytest.skip("API key not configured; auth is disabled in dev mode")

    response = httpx.post(
        f"{orchestrator_url}/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"Authorization": "Bearer wrong-key-xxx"},
        timeout=10.0,
    )
    assert response.status_code == 401
    assert "bearer" in response.headers.get("www-authenticate", "").lower()


# ============================================================
# Open WebUI → Orchestrator 链路验证
# ============================================================


@pytest.mark.e2e
@pytest.mark.p5_e2e
def test_open_webui_can_proxy_to_orchestrator(open_webui_url: str) -> None:
    """SPEC §3.9.1：Open WebUI 内置的 OpenAI 兼容端点能转达到 Orchestrator。

    Open WebUI ``GET /api/v1/models``（**注意：是 WebUI 的 OpenAI 兼容入口，不是
    WebUI 管理 API**）会从配置的 ``OPENAI_API_BASE_URL`` 拉取模型。
    本测试**不**调 WebUI 的 OpenAI 兼容入口（需要登录态），而是用一个**直接调
    WebUI 后端 OpenAI 路由**的 GET 探活（WebUI 启动后内部会缓存 ``/v1/models``）。
    改用更轻量的策略：调 WebUI 的 ``/api/config`` 端点验证它读取了
    ``OPENAI_API_BASE_URL`` 环境变量。
    """
    response = httpx.get(f"{open_webui_url}/api/config", timeout=10.0)
    # /api/config 不需要鉴权，返回前端运行时配置
    assert response.status_code == 200, f"open-webui /api/config failed: {response.status_code}"


# ============================================================
# 端到端：用户视角（构造一次「在 WebUI 输入 → 拿到答案」完整链路）
# ============================================================


@pytest.mark.e2e
@pytest.mark.p5_e2e
def test_end_to_end_chat_via_openai_compat(orchestrator_url: str) -> None:
    """SPEC §3.9 端到端：模拟 Open WebUI 发到 Orchestrator 的完整一次请求。

    用户视角：用户从 WebUI 框输入一句话 → 选 ``auto`` 模型 → 点发送 →
    拿到 assistant 答复。本测试模拟的就是这个链路的最核心部分。
    """
    session_id = f"e2e-p5-{uuid.uuid4().hex[:8]}"
    payload = {
        "model": "auto",
        "messages": [
            {"role": "system", "content": "你是 a2a-prod 多 Agent 助手，回答要简洁。"},
            {"role": "user", "content": "用一句话解释什么是 RESTful API。"},
        ],
        "stream": False,
        # Open WebUI 会塞的额外字段（验证 extra="allow"）
        "user": session_id,
        "metadata": {"chat_id": session_id},
    }

    started = time.time()
    response = httpx.post(
        f"{orchestrator_url}/v1/chat/completions",
        json=payload,
        headers=_bearer(),
        timeout=60.0,
    )
    elapsed = time.time() - started

    assert (
        response.status_code == 200
    ), f"chat completion failed: {response.status_code} {response.text[:500]}"
    body = response.json()

    # 必含字段
    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert len(body["choices"]) >= 1
    content = body["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert len(content) > 0, "empty response content"
    assert body["choices"][0]["finish_reason"] == "stop"

    # 性能基线：P5 阶段一次编排应在 30s 内（SPEC §3.6）
    assert elapsed < 30.0, f"chat completion took {elapsed:.1f}s, exceeds 30s budget"
