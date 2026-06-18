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
    """SPEC §P2 编排模式。"""

    DIRECT = "direct"  # 直接路由：单 Agent
    TASK_DECOMPOSITION = "task_decomposition"  # 任务分解：多 Agent 并行
    WORKFLOW = "workflow"  # P2.1：SDLC 研发协作工作流（顺序串联 + 反馈回路）
    # NEGOTIATION 放 P2.2+（NORTH_STAR §3.2 "本阶段不做"）
    # NEGOTIATION = "negotiation"


# ============================================================
# 任务表示（任务分解模式拆出来的子任务）
# ============================================================


class SubTask(TypedDict, total=False):
    """任务分解模式下的子任务。"""

    description: str  # 子任务描述（发给下游 Agent 的 prompt）
    assigned_to: str  # AgentName 的值（如 "glm-agent"）


# ============================================================
# SDLC 工作流产出（P2.1 WORKFLOW 模式）
# ============================================================


class SdlcDoc(TypedDict, total=False):
    """SDLC 工作流各阶段产出（每个 value 是 Agent 输出的 markdown 文本）。"""

    spec: str  # DeepSeek 产出的 PRD/Spec
    tech_design: str  # GLM-5.2 产出的技术规范 + 编码指令
    implementation: str  # MiniMax 产出的实现说明（含 pytest 结果）
    code_paths: list[str]  # MiniMax 落盘的代码文件相对路径（workspace 内）


class SdlcFeedback(TypedDict, total=False):
    """单轮反馈记录（GLM-5.2 → MiniMax 的指导）。"""

    round: int  # 第几轮（1-based）
    blocker: str  # MiniMax 的 [NEED_HELP] 问题描述
    guidance: str  # GLM-5.2 给出的指导（非代码）


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

    # ---- 可观测性 ----
    _trace_context: dict[str, Any]  # Langfuse trace context（trace_id 等）

    # ---- WORKFLOW 模式专用（P2.1） ----
    sdlc_doc: SdlcDoc  # 各阶段产出文档（spec / tech_design / implementation / code_paths）
    sdlc_feedback: list[SdlcFeedback]  # 每轮反馈记录（GLM-5.2 → MiniMax 的指导）
    feedback_rounds: int  # 已发生的反馈轮数（0/1/2，上限 MAX_ROUNDS=2）
    workflow_status: str  # "running" / "blocked_resolved" / "blocked_unresolved"

    # ---- 消息历史（保留扩展空间，目前不用，但避免 P2.1 时改 schema） ----
    # 注：add_messages 是 LangGraph 内置 reducer，会 append 而非覆盖
    messages: Annotated[list[Any], add_messages]
