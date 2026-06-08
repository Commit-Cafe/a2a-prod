"""Orchestrator 状态 schema（LangGraph StateGraph）。

定义 LangGraph 节点之间共享的状态对象。每个节点接收完整 state，
返回 partial update（仅修改的 key），LangGraph 自动 merge。

参考：https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages

# ============================================================
# 路由目标 Agent 标识（与 docker-compose service 名对齐）
# ============================================================


class AgentName(StrEnum):
    """可被 Orchestrator 调用的下游 Agent 标识。"""

    GLM = "glm-agent"
    DEEPSEEK = "deepseek-agent"
    MINIMAX = "minimax-agent"


class OrchestrationMode(StrEnum):
    """SPEC §P2 编排模式（本阶段实装前 2 种）。"""

    DIRECT = "direct"  # 直接路由：单 Agent
    TASK_DECOMPOSITION = "task_decomposition"  # 任务分解：多 Agent 并行
    # 以下两种放 P2.1+（NORTH_STAR §3.2 "本阶段不做"）
    # NEGOTIATION = "negotiation"
    # WORKFLOW = "workflow"


# ============================================================
# 任务表示（任务分解模式拆出来的子任务）
# ============================================================


class SubTask(TypedDict, total=False):
    """任务分解模式下的子任务。"""

    description: str  # 子任务描述（发给下游 Agent 的 prompt）
    assigned_to: str  # AgentName 的值（如 "glm-agent"）


# ============================================================
# 共享状态（LangGraph StateGraph 的 State schema）
# ============================================================


class OrchestrationState(TypedDict, total=False):
    """Orchestrator 主图共享状态。

    LangGraph 要求 state 是 TypedDict。``total=False`` 表示所有 key 可选，
    便于节点返回 partial update。
    """

    # ---- 原始输入 ----
    user_query: str  # 用户原始问题
    session_id: str  # 会话 ID（用于日志/trace 关联）
    user_id: str  # 业务用户 ID（透传到 Langfuse trace metadata）

    # ---- classifier 节点输出 ----
    mode: str  # OrchestrationMode 的值
    subtasks: list[SubTask]  # 任务分解模式下拆出来的子任务

    # ---- router 节点输出（仅 DIRECT 模式） ----
    target_agent: str  # AgentName 的值

    # ---- executor 节点输出 ----
    # 多 Agent 并行结果，key=AgentName.value，value=Agent 返回的文本
    agent_responses: dict[str, str]

    # ---- aggregate 节点输出 ----
    final_answer: str  # 最终汇总后的回答

    # ---- 错误状态 ----
    errors: list[str]  # 节点内捕获的异常信息（不阻塞图执行）

    # ---- 消息历史（保留扩展空间，目前不用，但避免 P2.1 时改 schema） ----
    # 注：add_messages 是 LangGraph 内置 reducer，会 append 而非覆盖
    messages: Annotated[list[Any], add_messages]
