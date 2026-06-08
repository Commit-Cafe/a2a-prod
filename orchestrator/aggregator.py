"""Aggregator 节点：合并多 Agent 响应为最终答案。

两种场景：
1. **单 Agent 响应**（DIRECT 模式）：直接透传，无聚合
2. **多 Agent 响应**（DECOMPOSITION 模式）：拼接，按固定 Agent 顺序

P2 阶段策略：纯文本拼接（结构清晰，便于 e2e 验证）。
P2.1 升级：用 LLM 做语义聚合（输入多 Agent 回答，输出综合答案）。
"""

from __future__ import annotations

from typing import Any

import structlog

from observability.tracing import trace_node
from orchestrator.state import AgentName, OrchestrationState

logger = structlog.get_logger(__name__)


# 拼接顺序：GLM → DeepSeek → MiniMax（通用 → 专精）
_AGENT_ORDER: tuple[str, ...] = (
    AgentName.GLM.value,
    AgentName.DEEPSEEK.value,
    AgentName.MINIMAX.value,
)

# Agent 显示名（中文，对齐 SPEC §2.2 三 Agent 人设）
_AGENT_DISPLAY: dict[str, str] = {
    AgentName.GLM.value: "GLM（代码审查）",
    AgentName.DEEPSEEK.value: "DeepSeek（需求/方案）",
    AgentName.MINIMAX.value: "MiniMax（代码实现）",
}


def aggregate(state: OrchestrationState) -> dict[str, Any]:
    """LangGraph 节点：合并 agent_responses 为 final_answer。"""
    responses: dict[str, str] = state.get("agent_responses", {}) or {}
    errors: list[str] = state.get("errors", []) or []
    session_id = state.get("session_id", "-")

    log = logger.bind(session_id=session_id, response_count=len(responses), error_count=len(errors))

    # 场景 1：单 Agent 响应 → 直接透传
    if len(responses) <= 1 and not errors:
        if not responses:
            log.warning("aggregate_no_response")
            return {"final_answer": "(无 Agent 响应)"}
        text = next(iter(responses.values()))
        log.info("aggregate_passthrough")
        return {"final_answer": text}

    # 场景 2：多 Agent 响应 → 按固定顺序拼接
    sections: list[str] = []
    for agent_name in _AGENT_ORDER:
        if agent_name not in responses:
            continue
        display = _AGENT_DISPLAY.get(agent_name, agent_name)
        sections.append(f"## {display}\n\n{responses[agent_name]}")

    # 兜底：处理 _AGENT_ORDER 没覆盖的 Agent
    for agent_name, text in responses.items():
        if agent_name in _AGENT_ORDER:
            continue
        sections.append(f"## {agent_name}\n\n{text}")

    # 错误附录（如有）
    if errors:
        sections.append("## ⚠️ 执行错误\n\n" + "\n".join(f"- {e}" for e in errors))

    final = "\n\n---\n\n".join(sections)
    log.info("aggregate_merged", section_count=len(sections), final_length=len(final))
    return {"final_answer": final}


aggregate = trace_node(name="orchestrator.aggregate")(aggregate)
