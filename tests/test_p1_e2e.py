"""P1 阶段 e2e 测试。

前置条件：
- ``.env.prod`` 已填好三家 API Key
- 已运行 ``docker compose --env-file .env.prod up -d`` 启动 litellm + 3 Agent
- 四个容器全部 healthy（用 ``docker compose ps`` 检查）

测试内容：
- 三 Agent 的 ``/.well-known/agent.json`` 端点（结构校验）
- 三 Agent 的 ``message/send``（A2A JSON-RPC，验证响应结构 + status.state == completed）
- 三 Agent 的 ``message/stream``（SSE，验证至少收到一个 event）

标记：``@pytest.mark.e2e``（默认不跑，需 ``pytest -m e2e`` 显式触发）
原因：e2e 会真实调用 LLM API 消耗额度，且依赖容器健康。

用法：
    # 仅跑契约测试（默认）
    pytest

    # 跑 e2e（需先 docker compose up）
    pytest -m e2e

    # 全跑
    pytest -m "e2e or not e2e"
"""

from __future__ import annotations

import uuid

import httpx
import pytest

# 三 Agent 公共测试参数：URL fixture 名
AGENT_TARGETS = [
    pytest.param("glm_agent_url", id="glm"),
    pytest.param("deepseek_agent_url", id="deepseek"),
    pytest.param("minimax_agent_url", id="minimax"),
]

# 每个 Agent 的 message/send prompt（简单 + 不耗太多 token）
SEND_PROMPTS: dict[str, str] = {
    "glm_agent_url": "你好，用一句话自我介绍",
    "deepseek_agent_url": "1+1 等于几？请只回答数字",
    "minimax_agent_url": "用 Python 写一个 hello world",
}

# 每个 Agent 的 message/stream prompt（与 send 不同，便于人工检查流式输出）
STREAM_PROMPTS: dict[str, str] = {
    "glm_agent_url": "用一句话介绍 A2A 协议",
    "deepseek_agent_url": "2 的 10 次方等于多少？请只回答数字",
    "minimax_agent_url": "写一个 Python 函数返回 hello",
}


# ============================================================
# Agent Card 端点（ /.well-known/agent.json）
# ============================================================


@pytest.mark.e2e
@pytest.mark.parametrize("agent_url_fixture", AGENT_TARGETS)
def test_agent_card_endpoint(request: pytest.FixtureRequest, agent_url_fixture: str) -> None:
    """SPEC §1.2：``/.well-known/agent.json`` MUST 返回 Agent Card JSON。"""
    base_url: str = request.getfixturevalue(agent_url_fixture)
    response = httpx.get(f"{base_url}/.well-known/agent.json", timeout=10.0)
    assert response.status_code == 200
    card = response.json()
    # SPEC §1.1 MUST 字段
    assert isinstance(card.get("name"), str) and card["name"]
    assert isinstance(card.get("description"), str) and card["description"]
    assert isinstance(card.get("version"), str)
    assert isinstance(card.get("url"), str)
    assert isinstance(card.get("skills"), list) and len(card["skills"]) >= 1


# ============================================================
# message/send（同步）
# ============================================================


def _build_send_payload(text: str) -> dict[str, object]:
    """构造 A2A JSON-RPC message/send 请求体。"""
    return {
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
                "messageId": f"msg-{uuid.uuid4().hex[:12]}",
                "kind": "message",
            }
        },
        "id": 1,
    }


@pytest.mark.e2e
@pytest.mark.parametrize("agent_url_fixture", AGENT_TARGETS)
def test_message_send(request: pytest.FixtureRequest, agent_url_fixture: str) -> None:
    """SPEC §1.3：``message/send`` MUST 返回 status.state == completed 的 Task。"""
    base_url: str = request.getfixturevalue(agent_url_fixture)
    # 不同 agent 用不同 prompt（已在 AGENT_TARGETS 里定义）
    prompt = {
        "glm_agent_url": "你好，用一句话自我介绍",
        "deepseek_agent_url": "1+1 等于几？请只回答数字",
        "minimax_agent_url": "用 Python 写一个 hello world",
    }[agent_url_fixture]

    payload = _build_send_payload(prompt)
    response = httpx.post(base_url, json=payload, timeout=120.0)
    assert response.status_code == 200, f"HTTP {response.status_code}: {response.text[:300]}"

    body = response.json()
    # JSON-RPC 错误
    assert "error" not in body, f"JSON-RPC error: {body.get('error')!r}"
    assert "result" in body, f"missing result: {body!r}"

    task = body["result"]
    assert isinstance(task, dict)
    # SPEC §1.3：MUST 返回 Task 对象
    assert task.get("kind") == "task", f"unexpected kind: {task.get('kind')!r}"
    # 状态：completed（也可能 failed，但 e2e 应该 completed）
    status = task.get("status")
    assert isinstance(status, dict)
    state = status.get("state")
    assert state == "completed", (
        f"task not completed: state={state!r}, message={status.get('message')!r}"
    )


# ============================================================
# message/stream（SSE）
# ============================================================


def _build_stream_payload(text: str) -> dict[str, object]:
    """构造 A2A JSON-RPC message/stream 请求体。"""
    return {
        "jsonrpc": "2.0",
        "method": "message/stream",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
                "messageId": f"msg-{uuid.uuid4().hex[:12]}",
                "kind": "message",
            }
        },
        "id": 2,
    }


@pytest.mark.e2e
@pytest.mark.parametrize("agent_url_fixture", AGENT_TARGETS)
def test_message_stream(request: pytest.FixtureRequest, agent_url_fixture: str) -> None:
    """SPEC §1.3 / §1.4：``message/stream`` MUST 通过 SSE 返回至少一个 event。

    不验证中间块的具体文本（LLM 输出不可预测），只验证：
    1. Content-Type 是 text/event-stream
    2. 至少收到一个 ``data:`` 行
    3. 最终事件包含 status.state == completed
    """
    base_url: str = request.getfixturevalue(agent_url_fixture)
    prompt = STREAM_PROMPTS[agent_url_fixture]

    payload = _build_stream_payload(prompt)

    # httpx 流式：with stream 切到上下文
    data_lines: list[str] = []
    final_state: str | None = None
    with httpx.stream(
        "POST", base_url, json=payload, timeout=180.0
    ) as response:
        assert response.status_code == 200, (
            f"HTTP {response.status_code}"
        )
        ct = response.headers.get("content-type", "")
        assert "text/event-stream" in ct, f"unexpected content-type: {ct!r}"

        for line in response.iter_lines():
            if not line:
                continue
            if line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())

    # 至少收到一个 data 行
    assert data_lines, "no SSE data event received"

    # 解析最后一个事件（应当是终态）
    import json as _json

    last_event: dict[str, object] = {}
    for raw in reversed(data_lines):
        if not raw:
            continue
        try:
            last_event = _json.loads(raw)
            break
        except Exception:
            continue

    if "result" in last_event:
        result = last_event["result"]
        if isinstance(result, dict):
            status = result.get("status")
            if isinstance(status, dict):
                final_state = status.get("state")

    # 容忍 working 中间态，但最后必须有 completed
    assert final_state == "completed", (
        f"final state not completed: {final_state!r}, last_event={last_event!r}"
    )
