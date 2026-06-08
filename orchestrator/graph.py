"""LangGraph StateGraph 主图组装。

图结构（mermaid）：

    START → classify → {?}
                          ├─ direct ──→ direct_execute ──→ aggregate → END
                          └─ decompose → decompose_execute → aggregate → END

注：本图采用 conditional_edges 实现"分支"，所有路径都汇聚到 aggregate 节点。
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from observability.tracing import trace_node
from orchestrator.aggregator import aggregate
from orchestrator.classifier import classify, route_after_classify
from orchestrator.executor import decompose_execute, direct_execute
from orchestrator.state import OrchestrationState

# ============================================================
# 图构建（compile 一次，复用多次）
# ============================================================


def build_graph() -> Any:
    """构建 LangGraph 主图并 compile。

    Returns:
        compiled LangGraph 实例（有 ``ainvoke`` / ``astream`` 方法）
    """
    graph = StateGraph(OrchestrationState)

    # 1) 添加节点
    graph.add_node("classify", classify)
    graph.add_node("direct_execute", direct_execute)
    graph.add_node("decompose_execute", decompose_execute)
    graph.add_node("aggregate", aggregate)

    # 2) 边
    graph.add_edge(START, "classify")

    # classify 后条件分支
    graph.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            "direct": "direct_execute",
            "decompose": "decompose_execute",
        },
    )

    # 两条分支都汇聚到 aggregate
    graph.add_edge("direct_execute", "aggregate")
    graph.add_edge("decompose_execute", "aggregate")

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
) -> dict[str, Any]:
    """同步执行编排（等所有节点跑完一次性返回）。

    Args:
        user_query: 用户原始问题
        session_id: 会话 ID（用于日志关联；不传则自动生成）
        user_id: 业务用户 ID（透传到 Langfuse trace metadata）

    Returns:
        最终 state（含 ``final_answer`` / ``mode`` / ``agent_responses`` 等）
    """
    import uuid

    state_input: OrchestrationState = {
        "user_query": user_query,
        "session_id": session_id or f"orch-{uuid.uuid4().hex[:12]}",
        "user_id": user_id or "a2a-user",
    }
    graph = get_compiled_graph()
    result = await graph.ainvoke(state_input)
    # LangGraph ainvoke 返回 dict[str, Any]，与函数签名一致
    return dict(result)


# P4: 顶层入口也加 trace（包整个图的执行）
orchestrate = trace_node(name="orchestrator.orchestrate")(orchestrate)
