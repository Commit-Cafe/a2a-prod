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
from orchestrator.sdlc_workflow import NEED_HELP_MARKER, get_max_feedback_rounds
from orchestrator.state import (
    AgentName,
    OrchestrationMode,
    OrchestrationState,
    SdlcDoc,
    SdlcFeedback,
)

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
    """LangGraph 节点：合并 agent_responses 为 final_answer。

    P2.1 新增：mode=workflow 时走专属聚合 ``_aggregate_workflow``（spec §5.6），
    其余走原有 DIRECT/DECOMPOSITION 逻辑。
    """
    mode = state.get("mode", "")

    # P2.1：WORKFLOW 模式专属聚合
    if mode == OrchestrationMode.WORKFLOW.value:
        return _aggregate_workflow(state)

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


# ============================================================
# P2.1：WORKFLOW 模式专属聚合（spec §5.6）
# ============================================================


def _aggregate_workflow(state: OrchestrationState) -> dict[str, Any]:
    """WORKFLOW 模式专属聚合：按研发流程顺序拼接各阶段产出。

    拼接顺序：Spec（DeepSeek）→ 技术规范（GLM）→ 反馈回路记录 →
    实现（MiniMax）→ 落盘文件 → 错误附录 → 工作流状态总结。

    Returns:
        ``{"final_answer": ..., "workflow_status": ...}``
    """
    doc: SdlcDoc = state.get("sdlc_doc", {}) or {}
    feedbacks: list[SdlcFeedback] = state.get("sdlc_feedback", []) or []
    errors: list[str] = state.get("errors", []) or []
    rounds = state.get("feedback_rounds", 0)
    # S7 修复：函数级 getter（参见 sdlc_workflow.get_max_feedback_rounds）
    max_rounds = get_max_feedback_rounds()

    sections: list[str] = []

    # 1. Spec（DeepSeek）
    if doc.get("spec"):
        sections.append(f"## 📋 Spec（DeepSeek 产 PRD）\n\n{doc['spec']}")

    # 2. 技术规范（GLM-5.2）
    if doc.get("tech_design"):
        sections.append(f"## 🏗️ 技术规范（GLM-5.2）\n\n{doc['tech_design']}")

    # 3. 反馈回路记录
    for fb in feedbacks:
        sections.append(
            f"## 🔁 反馈轮次 {fb['round']}（GLM-5.2 → MiniMax）\n\n"
            f"**MiniMax 阻塞**：{fb['blocker']}\n\n"
            f"**GLM 指导**：{fb['guidance']}"
        )

    # 4. 实现（MiniMax）
    if doc.get("implementation"):
        impl = doc["implementation"]
        # N2 修复（GLM 2026-06-18 review）：用 rounds 判断状态而非重扫文本
        # （避免 MiniMax 在解释中引用 [NEED_HELP] 字符串导致误判 ⚠️）
        status_emoji = "⚠️" if rounds >= max_rounds else "✅"
        sections.append(f"## {status_emoji} 实现（MiniMax）\n\n{impl}")

    # 5. 产出文件清单
    code_paths = doc.get("code_paths") or []
    if code_paths:
        paths_md = "\n".join(f"- `{p}`" for p in code_paths)
        sections.append(f"## 📁 落盘文件\n\n{paths_md}")

    # 6. 错误附录
    if errors:
        sections.append("## ⚠️ 执行错误\n\n" + "\n".join(f"- {e}" for e in errors))

    # 7. 状态总结 + workflow_status 推导
    impl_text = doc.get("implementation", "")
    unresolved = NEED_HELP_MARKER in impl_text and rounds >= max_rounds
    if unresolved:
        status_summary = (
            f"⚠️ MiniMax 仍有阻塞但已达反馈上限" f"（{rounds}/{max_rounds}），需人工介入"
        )
        workflow_status = "blocked_unresolved"
    elif rounds > 0:
        status_summary = f"✅ 经 {rounds} 轮反馈后完成"
        workflow_status = "blocked_resolved"
    else:
        status_summary = "✅ 一次通过，无反馈"
        workflow_status = "blocked_resolved"

    sections.append(f"## 📊 工作流状态\n\n{status_summary}")

    final = "\n\n---\n\n".join(sections)
    return {"final_answer": final, "workflow_status": workflow_status}
