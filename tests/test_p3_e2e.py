"""P3 阶段 e2e 测试（3 Agent × 3 MCP server 真实联调）。

前置条件：
- ``.env.prod`` 已填好三家 API Key + MCP_FILESYSTEM_URL/MCP_FETCH_URL/MCP_SHELL_URL
- 已运行 ``docker compose --env-file .env.prod up -d`` 启动 litellm + 3 Agent + orchestrator + 3 MCP
- 全部容器 healthy

测试内容：
- 3 个 MCP server 的 /healthz 端点（探活）
- GLM Agent 调用 filesystem（read_file）读 workspace/samples/calc.py
- DeepSeek Agent 调用 filesystem（list_directory）探查 workspace 结构
- MiniMax Agent 调用 shell（run_command）跑 pytest 验证 calc.py

注意：LLM 输出不可预测，断言只验证「响应到达 + 包含工具调用证据（如文件名/代码片段/pytest 输出特征）」，
不验证 LLM 是否「真的」调用了 MCP tool（这部分由 contract 测试保障）。

标记：``@pytest.mark.e2e`` + ``@pytest.mark.p3_e2e``
- ``e2e``：conftest 第 1 轮探活 GLM Agent
- ``p3_e2e``：conftest 第 3 轮探活 3 个 MCP server

用法：
    # 仅跑 P3 e2e
    pytest -m "p3_e2e" tests/test_p3_e2e.py
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

# ============================================================
# MCP server 健康检查
# ============================================================


@pytest.mark.e2e
@pytest.mark.p3_e2e
def test_mcp_filesystem_healthz(mcp_filesystem_url: str) -> None:
    """SPEC §3.7.2：filesystem MCP server MUST 暴露 /healthz。"""
    response = httpx.get(f"{mcp_filesystem_url}/healthz", timeout=10.0)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "filesystem-mcp"


@pytest.mark.e2e
@pytest.mark.p3_e2e
def test_mcp_fetch_healthz(mcp_fetch_url: str) -> None:
    """SPEC §3.7.3：fetch MCP server MUST 暴露 /healthz。"""
    response = httpx.get(f"{mcp_fetch_url}/healthz", timeout=10.0)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "fetch-mcp"


@pytest.mark.e2e
@pytest.mark.p3_e2e
def test_mcp_shell_healthz(mcp_shell_url: str) -> None:
    """SPEC §3.7.4：shell MCP server MUST 暴露 /healthz。"""
    response = httpx.get(f"{mcp_shell_url}/healthz", timeout=10.0)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "shell-mcp"
    # allowlist 字段（SPEC §3.7.4）
    assert isinstance(body.get("allowlist"), list)
    assert set(body["allowlist"]) >= {"pytest", "ruff", "mypy", "git", "cat", "ls"}


# ============================================================
# MCP server 工具调用（直接 HTTP POST /mcp，跳过 Agent）
# ============================================================


def _mcp_tool_call(url: str, tool_name: str, arguments: dict[str, object]) -> Any:
    """构造一个 Streamable HTTP MCP 请求（JSON-RPC initialize + tools/call 二合一）。

    FastMCP ``stateless_http=True`` 时支持单次 POST 调用 tool，不需要 session。
    """
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
        "id": 1,
    }
    response = httpx.post(f"{url}/mcp", json=payload, timeout=60.0)
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        raise RuntimeError(f"MCP error: {body['error']}")
    return body


@pytest.mark.e2e
@pytest.mark.p3_e2e
def test_mcp_filesystem_read_samples_calc(mcp_filesystem_url: str) -> None:
    """直接调 filesystem MCP 的 read_file 读 workspace/samples/calc.py。"""
    result = _mcp_tool_call(
        mcp_filesystem_url,
        "read_file",
        {"path": "samples/calc.py"},
    )
    # MCP 返回的是 list[TextContent]，序列化后 isError + content
    assert "result" in result
    result_data: Any = result["result"]
    contents = result_data.get("content", [])
    assert contents, "empty content"
    text = str(contents[0].get("text", ""))
    # calc.py 含 add/subtract/multiply/divide/fibonacci
    assert "def add" in text or "def subtract" in text, f"unexpected content: {text[:200]}"


@pytest.mark.e2e
@pytest.mark.p3_e2e
def test_mcp_filesystem_path_escape_rejected(mcp_filesystem_url: str) -> None:
    """路径逃逸防护：直接调 read_file('../../etc/passwd') MUST 返回错误。"""
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "read_file", "arguments": {"path": "../../etc/passwd"}},
        "id": 1,
    }
    response = httpx.post(f"{mcp_filesystem_url}/mcp", json=payload, timeout=10.0)
    body = response.json()
    # 要么是 JSON-RPC error，要么是 result.isError=True
    has_error = "error" in body or (
        isinstance(body.get("result"), dict) and body["result"].get("isError")
    )
    assert has_error, f"path escape should be rejected, got: {body}"


@pytest.mark.e2e
@pytest.mark.p3_e2e
def test_mcp_shell_run_command_rejects_unknown(mcp_shell_url: str) -> None:
    """allowlist 防护：直接调 run_command('rm -rf /') MUST 返回错误。"""
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": "run_command", "arguments": {"command": "rm -rf /"}},
        "id": 1,
    }
    response = httpx.post(f"{mcp_shell_url}/mcp", json=payload, timeout=10.0)
    body = response.json()
    has_error = "error" in body or (
        isinstance(body.get("result"), dict) and body["result"].get("isError")
    )
    assert has_error, f"rm command should be rejected, got: {body}"


# ============================================================
# Agent × MCP 端到端联调（验证 Agent 能调通 MCP tool）
# ============================================================


def _build_send_payload(text: str) -> dict[str, object]:
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


def _extract_final_text(response_body: dict[str, object]) -> str:
    """从 A2A message/send 响应中抽取最终文本。"""
    result = response_body.get("result")
    if not isinstance(result, dict):
        return ""
    status = result.get("status") or {}
    if isinstance(status, dict) and status.get("state") != "completed":
        return ""
    artifacts = result.get("artifacts") or []
    chunks: list[str] = []
    for art in artifacts:
        if not isinstance(art, dict):
            continue
        for part in art.get("parts") or []:
            if isinstance(part, dict) and part.get("kind") == "text":
                chunks.append(str(part.get("text", "")))
    # 兜底：从 status.message.parts 取
    if not chunks:
        msg = status.get("message") if isinstance(status, dict) else None
        if isinstance(msg, dict):
            for part in msg.get("parts") or []:
                if isinstance(part, dict) and part.get("kind") == "text":
                    chunks.append(str(part.get("text", "")))
    return "\n".join(chunks)


@pytest.mark.e2e
@pytest.mark.p3_e2e
def test_glm_agent_uses_filesystem_read(glm_agent_url: str) -> None:
    """GLM Agent 应调 read_file 读 samples/calc.py 后做代码评审。

    LLM 输出不可预测，断言：
    - 任务 completed
    - 输出含 calc.py 的某些特征（函数名 / 文件名 / 代码块标记）
    """
    payload = _build_send_payload(
        "请用 read_file 工具读 workspace/samples/calc.py，"
        "然后给出 3 条具体的代码改进建议。"
    )
    response = httpx.post(glm_agent_url, json=payload, timeout=180.0)
    assert response.status_code == 200
    body = response.json()
    assert "error" not in body, f"JSON-RPC error: {body.get('error')!r}"

    task = body.get("result", {})
    status = task.get("status", {})
    assert status.get("state") == "completed", (
        f"task not completed: state={status.get('state')!r}"
    )

    text = _extract_final_text(body)
    # 至少 50 字（避免空响应）+ 含 calc.py 相关关键词
    assert len(text) >= 50, f"response too short: {text[:200]!r}"
    keywords = ["calc", "add", "subtract", "multiply", "divide", "fibonacci", "代码", "建议", "审查"]
    assert any(kw.lower() in text.lower() for kw in keywords), (
        f"response missing calc-related keywords: {text[:300]!r}"
    )


@pytest.mark.e2e
@pytest.mark.p3_e2e
def test_deepseek_agent_uses_filesystem_list(deepseek_agent_url: str) -> None:
    """DeepSeek Agent 应调 list_directory 探查 workspace 结构后做架构评估。"""
    payload = _build_send_payload(
        "请用 list_directory 工具查看 workspace 根目录下有什么，"
        "然后用一句话评估这个目录结构适不适合做代码仓库。"
    )
    response = httpx.post(deepseek_agent_url, json=payload, timeout=180.0)
    assert response.status_code == 200
    body = response.json()
    assert "error" not in body

    task = body.get("result", {})
    status = task.get("status", {})
    assert status.get("state") == "completed", (
        f"task not completed: state={status.get('state')!r}"
    )

    text = _extract_final_text(body)
    assert len(text) >= 30, f"response too short: {text[:200]!r}"
    # 应该提到 workspace/samples 或目录相关词
    keywords = ["workspace", "samples", "目录", "结构", "文件", "directory"]
    assert any(kw.lower() in text.lower() for kw in keywords), (
        f"response missing directory-related keywords: {text[:300]!r}"
    )


@pytest.mark.e2e
@pytest.mark.p3_e2e
def test_minimax_agent_uses_shell_pytest(minimax_agent_url: str) -> None:
    """MiniMax Agent 应调 run_command 跑 pytest 验证 samples/test_calc.py。"""
    payload = _build_send_payload(
        "请用 run_command 工具跑 `pytest samples/test_calc.py -v`，"
        "然后告诉我测试结果（通过 / 失败 / 数量）。"
    )
    response = httpx.post(minimax_agent_url, json=payload, timeout=180.0)
    assert response.status_code == 200
    body = response.json()
    assert "error" not in body

    task = body.get("result", {})
    status = task.get("status", {})
    assert status.get("state") == "completed", (
        f"task not completed: state={status.get('state')!r}"
    )

    text = _extract_final_text(body)
    assert len(text) >= 30, f"response too short: {text[:200]!r}"
    # 应该提到 pytest / 测试 / passed / failed / 通过 / 失败
    keywords = ["pytest", "passed", "failed", "测试", "通过", "失败", "test"]
    assert any(kw.lower() in text.lower() for kw in keywords), (
        f"response missing pytest-related keywords: {text[:300]!r}"
    )
