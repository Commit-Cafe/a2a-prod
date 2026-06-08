"""Orchestrator Host — FastAPI 编排入口（P2 阶段实现）。

P0+P1 阶段此包为空，三 Agent 直接对客户端暴露 A2A 端点。
P2 阶段在此实现 LangGraph StateGraph 主图，对外暴露 OpenAI 兼容 /v1/chat/completions。
"""
