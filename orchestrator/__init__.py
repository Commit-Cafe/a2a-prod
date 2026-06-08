"""LangGraph 编排引擎（P2 阶段实装；P5 起扩展 OpenAI 兼容层）。

本阶段实装：
- ``state`` - StateGraph 状态 schema（OrchestrationState / SubTask / AgentName）
- ``classifier`` - 分类节点（关键词启发式路由，P2.1 升级 LLM）
- ``executor`` - 执行节点（A2A JSON-RPC 调用，DIRECT / DECOMPOSITION 两种模式）
- ``aggregator`` - 聚合节点（单 Agent 透传 / 多 Agent 拼接）
- ``a2a_client`` - A2A JSON-RPC 客户端（httpx + structlog）
- ``graph`` - 主图组装 + ``orchestrate()`` 入口
- ``openai_compat`` - OpenAI 兼容层 Pydantic schema + 转换函数（P5 引入）
- ``__main__`` - FastAPI Host（端口 12080；P5 起增加 OpenAI 兼容端点）

未实装（NORTH_STAR §3.2 本阶段不做）：
- 协商模式（NEGOTIATION）：P2.1
- 工作流模式（WORKFLOW）：P2.2
- LLM 智能路由：P2.1
- LLM 语义聚合：P2.1
"""

from __future__ import annotations

from orchestrator.graph import build_graph, get_compiled_graph, orchestrate

__all__ = [
    "build_graph",
    "get_compiled_graph",
    "orchestrate",
]
