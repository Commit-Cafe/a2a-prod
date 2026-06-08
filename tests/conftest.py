"""pytest 全局 fixtures。

P0 阶段提供基础 fixture（端点 URL、httpx 客户端）。
P1 阶段补充三 Agent 的 Agent Card fixture + e2e 探活 skip 逻辑。
P2 阶段补充 Orchestrator URL + 探活（orchestrator 不通时单独 skip P2 e2e）。
P3 阶段补充 3 个 MCP server URL + 探活。
P4 阶段补充 Langfuse web URL + 探活。
P5 阶段补充 Open WebUI URL + 探活。
"""

from __future__ import annotations

import os

import httpx
import pytest


@pytest.fixture(scope="session")
def litellm_base_url() -> str:
    """LiteLLM Proxy 基础 URL。"""
    return f"http://localhost:{os.getenv('LITELLM_PROXY_PORT', '4000')}"


@pytest.fixture(scope="session")
def glm_agent_url() -> str:
    """GLM Agent 基础 URL。"""
    return f"http://localhost:{os.getenv('GLM_AGENT_PORT', '12001')}"


@pytest.fixture(scope="session")
def deepseek_agent_url() -> str:
    """DeepSeek Agent 基础 URL。"""
    return f"http://localhost:{os.getenv('DEEPSEEK_AGENT_PORT', '12002')}"


@pytest.fixture(scope="session")
def minimax_agent_url() -> str:
    """MiniMax Agent 基础 URL。"""
    return f"http://localhost:{os.getenv('MINIMAX_AGENT_PORT', '12003')}"


@pytest.fixture(scope="session")
def orchestrator_url() -> str:
    """Orchestrator 基础 URL（P2 引入）。"""
    return f"http://localhost:{os.getenv('ORCHESTRATOR_PORT', '12080')}"


@pytest.fixture(scope="session")
def mcp_filesystem_url() -> str:
    """Filesystem MCP server 基础 URL（P3 引入）。"""
    return f"http://localhost:{os.getenv('MCP_FILESYSTEM_PORT', '12101')}"


@pytest.fixture(scope="session")
def mcp_fetch_url() -> str:
    """Fetch MCP server 基础 URL（P3 引入）。"""
    return f"http://localhost:{os.getenv('MCP_FETCH_PORT', '12102')}"


@pytest.fixture(scope="session")
def mcp_shell_url() -> str:
    """Shell MCP server 基础 URL（P3 引入）。"""
    return f"http://localhost:{os.getenv('MCP_SHELL_PORT', '12103')}"


@pytest.fixture(scope="session")
def langfuse_web_url() -> str:
    """Langfuse Web 基础 URL（P4 引入）。"""
    return f"http://localhost:{os.getenv('LANGFUSE_PORT', '3000')}"


@pytest.fixture(scope="session")
def open_webui_url() -> str:
    """Open WebUI 基础 URL（P5 引入）。

    端口与 ``infra/docker-compose.yml::open-webui.ports`` 一致。
    """
    return f"http://localhost:{os.getenv('OPEN_WEBUI_PORT', '8080')}"


# ============================================================
# e2e 探活：docker compose 未启动时自动 skip 所有 e2e 测试
# ============================================================


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """收集完测试后做五轮探活：
    1) 任何 e2e 测试存在 → ping GLM Agent；不通则 skip 全部 e2e
    2) P2 e2e（标记 ``p2_e2e``）存在 → 再 ping Orchestrator；不通则 skip P2 e2e
    3) P3 e2e（标记 ``p3_e2e``）存在 → 再 ping 3 个 MCP server；不通则 skip P3 e2e
    4) P4 e2e（标记 ``p4_e2e``）存在 → 再 ping Langfuse web；不通则 skip P4 e2e
    5) P5 e2e（标记 ``p5_e2e``）存在 → 再 ping Open WebUI；不通则 skip P5 e2e
    """
    e2e_items = [item for item in items if item.get_closest_marker("e2e") is not None]
    if not e2e_items:
        return

    # ---- 第 1 轮：ping GLM Agent ----
    glm_url = f"http://localhost:{os.getenv('GLM_AGENT_PORT', '12001')}"
    try:
        r = httpx.get(f"{glm_url}/.well-known/agent.json", timeout=2.0)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001 - 探活，任何异常都 skip
        skip_reason = f"docker compose 未启动或 GLM Agent 不可达：{type(e).__name__}: {e}"
        skip_marker = pytest.mark.skip(reason=skip_reason)
        for item in e2e_items:
            item.add_marker(skip_marker)
        return  # GLM 都不通，下面也不用探了

    # ---- 第 2 轮：ping Orchestrator（仅 P2 e2e）----
    p2_items = [item for item in e2e_items if item.get_closest_marker("p2_e2e") is not None]
    if p2_items:
        orch_url = f"http://localhost:{os.getenv('ORCHESTRATOR_PORT', '12080')}"
        try:
            r = httpx.get(f"{orch_url}/health", timeout=3.0)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001 - 探活，任何异常都 skip
            skip_reason = f"Orchestrator 不可达：{type(e).__name__}: {e}"
            skip_marker = pytest.mark.skip(reason=skip_reason)
            for item in p2_items:
                item.add_marker(skip_marker)

    # ---- 第 3 轮：ping 3 个 MCP server（仅 P3 e2e）----
    p3_items = [item for item in e2e_items if item.get_closest_marker("p3_e2e") is not None]
    if p3_items:
        mcp_endpoints = [
            ("filesystem", f"http://localhost:{os.getenv('MCP_FILESYSTEM_PORT', '12101')}/healthz"),
            ("fetch", f"http://localhost:{os.getenv('MCP_FETCH_PORT', '12102')}/healthz"),
            ("shell", f"http://localhost:{os.getenv('MCP_SHELL_PORT', '12103')}/healthz"),
        ]
        for name, url in mcp_endpoints:
            try:
                r = httpx.get(url, timeout=3.0)
                r.raise_for_status()
            except Exception as e:  # noqa: BLE001 - 探活，任何异常都 skip
                skip_reason = f"MCP {name} server 不可达：{type(e).__name__}: {e}"
                skip_marker = pytest.mark.skip(reason=skip_reason)
                for item in p3_items:
                    item.add_marker(skip_marker)
                return

    # ---- 第 4 轮：ping Langfuse web（仅 P4 e2e）----
    p4_items = [item for item in e2e_items if item.get_closest_marker("p4_e2e") is not None]
    if p4_items:
        langfuse_url = f"http://localhost:{os.getenv('LANGFUSE_PORT', '3000')}/api/public/health"
        try:
            r = httpx.get(langfuse_url, timeout=5.0)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001 - 探活，任何异常都 skip
            skip_reason = f"Langfuse web 不可达：{type(e).__name__}: {e}"
            skip_marker = pytest.mark.skip(reason=skip_reason)
            for item in p4_items:
                item.add_marker(skip_marker)

    # ---- 第 5 轮：ping Open WebUI（仅 P5 e2e）----
    p5_items = [item for item in e2e_items if item.get_closest_marker("p5_e2e") is not None]
    if p5_items:
        webui_url = f"http://localhost:{os.getenv('OPEN_WEBUI_PORT', '8080')}/health"
        try:
            r = httpx.get(webui_url, timeout=5.0)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001 - 探活，任何异常都 skip
            skip_reason = f"Open WebUI 不可达：{type(e).__name__}: {e}"
            skip_marker = pytest.mark.skip(reason=skip_reason)
            for item in p5_items:
                item.add_marker(skip_marker)

