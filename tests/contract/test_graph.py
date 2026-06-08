"""LangGraph 图集成测试（SPEC §P2 主图）。

校验内容：
1. ``build_graph`` 返回编译后的图（有 ainvoke）
2. ``orchestrate`` DIRECT 模式：mock a2a_client，验证只调一次
3. ``orchestrate`` DECOMPOSITION 模式：mock a2a_client，验证并行调三次
4. 错误隔离：单 Agent 失败不阻塞图

Mock 策略：
- ``unittest.mock.patch`` 替换 ``orchestrator.a2a_client.message_send``
- 不真实调下游（避免依赖 docker）
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator import a2a_client
from orchestrator.graph import build_graph, get_compiled_graph, orchestrate
from orchestrator.state import AgentName, OrchestrationMode


@pytest.fixture(autouse=True)
def _reset_graph_singleton() -> Iterator[None]:
    """每个测试前重置 graph 单例，避免 state 跨测试污染。"""
    import orchestrator.graph as graph_mod

    graph_mod._COMPILED_GRAPH = None
    yield
    graph_mod._COMPILED_GRAPH = None


# ============================================================
# Test 1：build_graph 编译
# ============================================================


class TestBuildGraph:
    """``build_graph`` 返回编译后的图。"""

    def test_returns_compiled_graph(self) -> None:
        graph = build_graph()
        # 编译后的图必须有 ainvoke 方法
        assert hasattr(graph, "ainvoke")
        assert hasattr(graph, "astream")

    def test_get_compiled_graph_singleton(self) -> None:
        """``get_compiled_graph`` 惰性初始化、复用。"""
        g1 = get_compiled_graph()
        g2 = get_compiled_graph()
        assert g1 is g2

    def test_build_graph_multiple_calls_returns_new_instance(self) -> None:
        """``build_graph`` 每次返回新实例（便于测试隔离）。"""
        g1 = build_graph()
        g2 = build_graph()
        assert g1 is not g2


# ============================================================
# Test 2：orchestrate DIRECT 模式
# ============================================================


class TestOrchestrateDirectMode:
    """DIRECT 模式：单 Agent 调用。"""

    async def test_requirement_query_routes_to_deepseek(self) -> None:
        """PM/CTO 类问题（技术选型）→ DeepSeek → 单次调用 → 答案透传。"""
        with patch.object(a2a_client, "message_send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = "技术选型方案：推荐 PostgreSQL"

            state = await orchestrate("请帮我做技术选型", session_id="test")

        assert state["mode"] == OrchestrationMode.DIRECT.value
        assert state["target_agent"] == AgentName.DEEPSEEK.value
        assert state["final_answer"] == "技术选型方案：推荐 PostgreSQL"
        # 只调用一次（DIRECT 模式）
        assert mock_send.await_count == 1
        # errors 应为空
        assert state.get("errors", []) == []

    async def test_code_query_routes_to_minimax(self) -> None:
        with patch.object(a2a_client, "message_send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = "def hello(): pass"

            state = await orchestrate("帮我重构这段代码")

        assert state["target_agent"] == AgentName.MINIMAX.value
        assert state["final_answer"] == "def hello(): pass"
        assert mock_send.await_count == 1

    async def test_general_query_routes_to_glm(self) -> None:
        with patch.object(a2a_client, "message_send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = "通用回答"

            state = await orchestrate("你好，介绍一下量子计算")

        assert state["target_agent"] == AgentName.GLM.value
        assert state["final_answer"] == "通用回答"

    async def test_direct_with_agent_error_records_in_errors(self) -> None:
        """DIRECT 模式下 Agent 调用失败：错误进 errors，不阻塞。

        aggregator 行为：errors 非空时走多 Agent 路径，最终答案含错误附录。
        """
        with patch.object(a2a_client, "message_send", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = a2a_client.A2ATimeoutError("timeout")

            state = await orchestrate("请帮我做技术选型")

        assert state["mode"] == OrchestrationMode.DIRECT.value
        # 错误附录被加进 final_answer（含 ⚠️ 标记）
        assert "⚠️" in state["final_answer"]
        assert any("TimeoutError" in e or "timeout" in e for e in state.get("errors", []))
        # agent_responses 应为空（DIRECT 模式只调一个 Agent，失败了）
        assert state.get("agent_responses", {}) == {}


# ============================================================
# Test 3：orchestrate DECOMPOSITION 模式
# ============================================================


class TestOrchestrateDecompositionMode:
    """DECOMPOSITION 模式：多 Agent 并行调用。"""

    async def test_comparison_query_calls_three_agents(self) -> None:
        """对比类问题 → 触发 DECOMPOSITION → 并行调三个 Agent。"""
        with patch.object(a2a_client, "message_send", new_callable=AsyncMock) as mock_send:
            # 三个 Agent 返回不同内容（便于校验拼接顺序）
            async def _side_effect(url: str, text: str, **kwargs: Any) -> str:
                if "deepseek" in url:
                    return "逻辑角度的回答"
                if "minimax" in url:
                    return "工程角度的回答"
                return "通用角度的回答"

            mock_send.side_effect = _side_effect

            state = await orchestrate("对比 Python 和 Go 的优缺点")

        assert state["mode"] == OrchestrationMode.TASK_DECOMPOSITION.value
        # 调用三次（DECOMPOSITION）
        assert mock_send.await_count == 3
        # 三 Agent 都有响应
        responses: dict[str, str] = state["agent_responses"]
        assert len(responses) == 3
        # 最终答案包含三段
        final = state["final_answer"]
        assert "逻辑角度" in final
        assert "工程角度" in final
        assert "通用角度" in final

    async def test_decomposition_partial_failure(self) -> None:
        """DECOMPOSITION 模式下单个 Agent 失败：其他 Agent 仍能完成。"""
        with patch.object(a2a_client, "message_send", new_callable=AsyncMock) as mock_send:
            async def _side_effect(url: str, text: str, **kwargs: Any) -> str:
                if "deepseek" in url:
                    raise a2a_client.A2ATimeoutError("deepseek down")
                if "minimax" in url:
                    return "工程角度的回答"
                return "通用角度的回答"

            mock_send.side_effect = _side_effect

            state = await orchestrate("对比 Python 和 Go 的并发模型")

        # 仍然调用三次
        assert mock_send.await_count == 3
        # 两 Agent 成功响应
        responses: dict[str, str] = state["agent_responses"]
        assert len(responses) == 2
        assert "minimax-agent" in responses
        assert "glm-agent" in responses
        # DeepSeek 错误进 errors
        assert any("deepseek" in e.lower() or "TimeoutError" in e for e in state.get("errors", []))
        # 最终答案包含成功的两段 + 错误附录
        final = state["final_answer"]
        assert "工程角度" in final
        assert "通用角度" in final
        assert "⚠️" in final

    async def test_decomposition_all_fail(self) -> None:
        """全部 Agent 失败：仍要返回结构化结果（errors 满载）。"""
        with patch.object(a2a_client, "message_send", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = a2a_client.A2AHTTPError("all 500")

            state = await orchestrate("对比 A 和 B 的特性")

        assert mock_send.await_count == 3
        assert state["agent_responses"] == {}
        assert len(state["errors"]) == 3
        assert "⚠️" in state["final_answer"]


# ============================================================
# Test 4：session_id 自动生成
# ============================================================


class TestSessionIdHandling:
    """``orchestrate`` 自动生成 session_id。"""

    async def test_auto_generate_session_id(self) -> None:
        with patch.object(a2a_client, "message_send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = "ok"

            state = await orchestrate("你好")

        sid: str = state["session_id"]
        assert sid.startswith("orch-")
        assert len(sid) > len("orch-")

    async def test_explicit_session_id_preserved(self) -> None:
        with patch.object(a2a_client, "message_send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = "ok"

            state = await orchestrate("你好", session_id="my-session-123")

        assert state["session_id"] == "my-session-123"

    async def test_two_calls_generate_different_session_ids(self) -> None:
        with patch.object(a2a_client, "message_send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = "ok"

            s1 = await orchestrate("q1")
            s2 = await orchestrate("q2")

        assert s1["session_id"] != s2["session_id"]
