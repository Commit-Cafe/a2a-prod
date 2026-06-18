"""LangGraph StateGraph 主图组装。

图结构（mermaid）：

    START → classify → {?}
                          ├─ direct ──→ direct_execute ──→ aggregate → END
                          └─ decompose → decompose_execute → aggregate → END

注：本图采用 conditional_edges 实现"分支"，所有路径都汇聚到 aggregate 节点。
"""

from __future__ import annotations

import os
from typing import Any

from langgraph.graph import END, START, StateGraph

from observability.tracing import start_trace
from orchestrator.aggregator import aggregate
from orchestrator.classifier import classify, route_after_classify
from orchestrator.executor import decompose_execute, direct_execute
from orchestrator.sdlc_workflow import workflow_execute
from orchestrator.state import OrchestrationState

# ============================================================
# 图构建（compile 一次，复用多次）
# ============================================================


def build_graph() -> Any:
    """构建 LangGraph 主图并 compile。

    Returns:
        compiled LangGraph 实例（有 ``ainvoke`` / ``astream`` 方法）

    图结构（P2.1 新增 workflow 分支）::

        START → classify → ┬─ direct      → direct_execute   ─┐
                           ├─ decompose   → decompose_execute ─┤→ aggregate → END
                           └─ workflow    → workflow_execute  ─┘
    """
    graph = StateGraph(OrchestrationState)

    # 1) 添加节点
    graph.add_node("classify", classify)
    graph.add_node("direct_execute", direct_execute)
    graph.add_node("decompose_execute", decompose_execute)
    graph.add_node("workflow_execute", workflow_execute)  # P2.1
    graph.add_node("aggregate", aggregate)

    # 2) 边
    graph.add_edge(START, "classify")

    # classify 后条件分支（P2.1 新增 workflow 分支）
    graph.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            "direct": "direct_execute",
            "decompose": "decompose_execute",
            "workflow": "workflow_execute",  # P2.1
        },
    )

    # 三条分支都汇聚到 aggregate
    graph.add_edge("direct_execute", "aggregate")
    graph.add_edge("decompose_execute", "aggregate")
    graph.add_edge("workflow_execute", "aggregate")  # P2.1

    # aggregate → END
    graph.add_edge("aggregate", END)

    return graph.compile()


# 模块级单例：FastAPI 启动时构建一次（性能优化）
# 注：这不是 Singleton（CODESTYLE §4.2 禁 Singleton），而是模块常量；
#     测试时可通过 ``build_graph()`` 重新构建
_COMPILED_GRAPH: Any = None


def get_compiled_graph() -> Any:
    """获取编译好的图（惰性初始化）。"""
    global _COMPILED_GRAPH
    if _COMPILED_GRAPH is None:
        _COMPILED_GRAPH = build_graph()
    return _COMPILED_GRAPH


# ============================================================
# 入口方法（FastAPI 路由调用）
# ============================================================


async def orchestrate(
    user_query: str,
    *,
    session_id: str | None = None,
    user_id: str | None = None,
    target_agent: str | None = None,
) -> dict[str, Any]:
    """同步执行编排（等所有节点跑完一次性返回）。

    Args:
        user_query: 用户原始问题
        session_id: 会话 ID（用于日志关联；不传则自动生成）
        user_id: 业务用户 ID（透传到 Langfuse trace metadata）
        target_agent: B3 修复（GLM 2026-06-18 review）—— 强制 DIRECT 路由到该 Agent，
            跳过 ``classify`` 节点的关键词路由。OpenAI 兼容层（``/v1/chat/completions``）
            在用户传 ``model=glm-agent`` 等具体 Agent 名时设置。空值 / None 时走
            原分类逻辑。

    Returns:
        最终 state（含 ``final_answer`` / ``mode`` / ``agent_responses`` 等）
    """
    import asyncio
    import uuid

    state_input: OrchestrationState = {
        "user_query": user_query,
        "session_id": session_id or f"orch-{uuid.uuid4().hex[:12]}",
        "user_id": user_id or "a2a-user",
    }

    # B3 修复：当 target_agent 非空时，强制走 DIRECT 模式并锁定 target_agent，
    # 使 LangGraph 跳过 classify 节点（route_after_classify 仍会按 mode 走 direct_execute，
    # 但即便被绕过, target_agent 字段已锁定语义）。下游 /v1/chat/completions 的
    # response_model 也会基于这个 target_agent 回填。
    if target_agent:
        state_input["mode"] = "direct"
        state_input["target_agent"] = target_agent

    trace_ctx = start_trace(
        "orchestrator.orchestrate",
        session_id=state_input["session_id"],
        user_id=state_input.get("user_id"),
    )
    if trace_ctx:
        state_input["_trace_context"] = trace_ctx

    graph = get_compiled_graph()
    timeout = float(os.getenv("ORCHESTRATE_TIMEOUT_SECONDS", "300"))
    result = await asyncio.wait_for(graph.ainvoke(state_input), timeout=timeout)
    return dict(result)
