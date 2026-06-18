"""OpenAI 兼容层（P5 阶段，详见 ADR-0009 / SPEC §3.9）。

让 Orchestrator 对外暴露 OpenAI 兼容 ``/v1/chat/completions`` 与 ``/v1/models``
端点，以便 Open WebUI（以及任何 OpenAI 客户端）直接对接。

设计原则：

- **协议保真**：遵循 OpenAI Chat Completions API 2024-01 公开契约（不含 tool_calls
  / functions / multimodal 等本阶段用不上的字段），但字段命名 / 类型严格一致
- **可降级**：LiteLLM master key 校验失败时返回 401，与 OpenAI 官方行为一致
- **可观测**：所有请求走 ``@trace_node`` 上报 Langfuse（P4 阶段已实装）
- **无状态**：OpenAI 兼容层不做 session 持久化；session_id 由 client 提供
  （Open WebUI 会传 ``user`` 字段作为伪 user_id）

模块构成：

- :class:`ChatMessage` / :class:`ChatCompletionRequest` / :class:`ChatCompletionResponse`
  — 严格的 Pydantic schema（与 OpenAI 官方字段对齐）
- :func:`build_chat_completion_response` — 把 Orchestrator state 转 OpenAI 响应
- :func:`list_models` — 返回当前可路由的下游 Agent 列表
- :func:`extract_user_query` — 从 OpenAI messages 列表提取 query
- :func:`resolve_target_agent` — model 字段 → 实际 Agent 路由决策
- :func:`is_supported_model` — 判断 model 字段是否合法

API key 鉴权在 :mod:`orchestrator.__main__`（``verify_api_key``），
本模块不包含。

参考：
- https://platform.openai.com/docs/api-reference/chat
- https://github.com/open-webui/open-webui（Open WebUI 兼容 OpenAI /v1 协议）
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ============================================================
# Request Schema（OpenAI Chat Completions API 2024-01）
# ============================================================


class ChatMessage(BaseModel):
    """OpenAI 单条消息。

    只支持 ``role: user | assistant | system``；其他 role（tool / function）
    留 P5.1（SPEC §3.9.5 未来扩展点）。
    """

    model_config = ConfigDict(extra="allow")  # 兼容 Open WebUI 可能多塞的字段

    role: Literal["user", "assistant", "system"] = Field(..., description="消息角色")
    content: str = Field(..., description="消息文本内容")

    # 预留扩展：tool_calls / name 等字段本阶段不实现，Open WebUI 不会发


class ChatCompletionRequest(BaseModel):
    """OpenAI Chat Completions 请求体。

    Notes:
        ``model`` 字段本项目里用作「下游 Agent 选择」——传 ``glm-agent`` /
        ``deepseek-agent`` / ``minimax-agent`` / ``auto``（默认，让 Orchestrator
        路由）。这是 P5 阶段为与 Open WebUI 多 Model 切换对齐做的特殊处理，
        在 SPEC §3.9.3 详述。
    """

    model_config = ConfigDict(extra="allow")

    model: str = Field(
        default="auto",
        description=(
            "目标模型 / Agent。可选值："
            "'glm-agent' / 'deepseek-agent' / 'minimax-agent' / 'auto'（默认）"
        ),
    )
    messages: list[ChatMessage] = Field(..., min_length=1, description="对话消息列表")
    stream: bool = Field(default=False, description="是否流式返回（SPEC §3.9.4）")
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)


# ============================================================
# Response Schema
# ============================================================


class ResponseMessage(BaseModel):
    """OpenAI 响应里的 assistant 消息。"""

    role: Literal["assistant"] = "assistant"
    content: str = Field(..., description="模型输出的文本")


class UsageInfo(BaseModel):
    """Token 用量（本阶段为占位值，真实计费 LiteLLM 端处理）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ChatCompletionChoice(BaseModel):
    """单个 completion choice。"""

    index: int = 0
    message: ResponseMessage
    finish_reason: Literal["stop", "length", "content_filter"] | None = "stop"


class ChatCompletionResponse(BaseModel):
    """OpenAI Chat Completions 响应。"""

    id: str = Field(..., description="OpenAI 风格 chatcmpl-<uuid> id")
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(..., description="Unix timestamp（秒）")
    model: str = Field(..., description="实际路由到的下游 Agent 名")
    choices: list[ChatCompletionChoice]
    usage: UsageInfo = Field(default_factory=UsageInfo)


# ============================================================
# Models Endpoint Schema
# ============================================================


class ModelInfo(BaseModel):
    """OpenAI Model 对象（精简版）。"""

    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str = "a2a-prod"


class ModelListResponse(BaseModel):
    """OpenAI ``GET /v1/models`` 响应。"""

    object: Literal["list"] = "list"
    data: list[ModelInfo]


# ============================================================
# Streaming Schema（SSE 事件 data 字段）
# ============================================================


class DeltaContent(BaseModel):
    """流式增量内容块。"""

    content: str | None = None


class StreamChoice(BaseModel):
    """流式 choice（含 delta）。"""

    index: int = 0
    delta: DeltaContent
    finish_reason: Literal["stop", "length", "content_filter"] | None = None


class StreamChunk(BaseModel):
    """OpenAI 流式响应块（``chat.completion.chunk``）。"""

    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: list[StreamChoice]


# ============================================================
# 常量与配置
# ============================================================


# 三个 Agent 对应的 OpenAI 友好 model id（同时支持 kebab-case 与 snake_case）
SUPPORTED_MODELS: tuple[str, ...] = (
    "glm-agent",
    "deepseek-agent",
    "minimax-agent",
)
DEFAULT_MODEL = "auto"
"""auto = 让 Orchestrator 按关键词路由（DIRECT / DECOMPOSITION）"""


def is_supported_model(model: str) -> bool:
    """判断 model 字段是否为支持的 Agent 标识或 ``auto``。"""
    return model == DEFAULT_MODEL or model in SUPPORTED_MODELS


# ============================================================
# 转换函数
# ============================================================


def build_chat_completion_response(
    *,
    state: dict[str, Any],
    model: str,
) -> ChatCompletionResponse:
    """把 Orchestrator state 转为 OpenAI 兼容响应。

    Args:
        state: ``orchestrator.graph.orchestrate()`` 的返回 dict
        model: 实际路由的 Agent 名（响应里 echo 回去）

    Returns:
        :class:`ChatCompletionResponse` 实例

    Note:
        Open WebUI 会把 ``response.choices[0].message.content`` 直接展示给用户。
    """
    final_answer = str(state.get("final_answer", ""))
    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex[:24]}",
        created=int(time.time()),
        model=model,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=ResponseMessage(role="assistant", content=final_answer),
                finish_reason="stop",
            ),
        ],
    )


def list_models() -> ModelListResponse:
    """返回 :class:`ModelListResponse`，列出 3 个可路由 Agent。"""
    now = int(time.time())
    return ModelListResponse(
        data=[ModelInfo(id=agent, created=now, owned_by="a2a-prod") for agent in SUPPORTED_MODELS],
    )


def extract_user_query(messages: list[ChatMessage]) -> str:
    """从 OpenAI messages 列表中提取用户 query。

    策略（SPEC §3.9.3 + S8 修复）：

    1. 找到最后一条 ``role=user`` 的消息（作为主问题）
    2. 拼接所有 system 消息作为前缀（保留上下文）
    3. S8 修复：多轮（user 数 ≥ 2）时把历史 user/assistant 拼入，避免 Open WebUI
       多轮对话"失忆"。**单轮保持向后兼容**（直接返回 system + last_user，
       不加【当前问题】前缀），避免破坏现有契约测试 + 直调三方 LLM 的 prompt 形态。
       多轮上下文留 P5.1（带 role 标签的对话历史），本阶段先解决失忆问题。
    4. 如果全是 assistant（无 user），返回空串 → 上游 400
    """
    system_parts: list[str] = []
    history: list[tuple[str, str]] = []  # (role, content) 顺序
    last_user_text: str | None = None
    for msg in messages:
        if msg.role == "system":
            system_parts.append(msg.content)
        elif msg.role == "user":
            history.append(("user", msg.content))
            last_user_text = msg.content
        elif msg.role == "assistant":
            history.append(("assistant", msg.content))

    if last_user_text is None:
        return ""

    # 单轮（user 数 = 1）→ 向后兼容：不加【对话历史】/【当前问题】块
    if len(history) <= 1:
        if system_parts:
            return "\n".join(system_parts) + "\n\n" + last_user_text
        return last_user_text

    # 多轮（user 数 ≥ 2）→ S8 新行为：拼历史
    parts: list[str] = []
    if system_parts:
        parts.append("\n".join(system_parts))

    history_lines = [f"[{role}] {content}" for role, content in history[:-1]]
    parts.append("【对话历史】\n" + "\n".join(history_lines))
    parts.append(f"【当前问题】\n{last_user_text}")
    return "\n\n".join(parts)


def resolve_target_agent(
    requested_model: str,
    *,
    orchestrator_state: dict[str, Any] | None = None,
) -> str:
    """解析请求的 model 字段，决定实际路由的 Agent。

    决策表（SPEC §3.9.3）：

    +-----------------------------+----------------------------------------+
    | 请求的 model                | 实际路由                               |
    +=============================+========================================+
    | ``"auto"``（默认）          | 用 Orchestrator classifier 决策        |
    |                             | （DIRECT 单 Agent / DECOMPOSE 多 Agent）|
    +-----------------------------+----------------------------------------+
    | ``"glm-agent"`` 等 3 个具体 | 走 DIRECT 模式强制路由到该 Agent       |
    +-----------------------------+----------------------------------------+
    | 未知 model                  | 走 ``auto``（兜底，避免 Open WebUI     |
    |                             | 自定义 model 名导致 400）                |
    +-----------------------------+----------------------------------------+

    Returns:
        最终用于 Orchestrator state ``target_agent`` 的 Agent 名；
        若为 ``auto`` 则返回空串（让 Orchestrator 自由路由）
    """
    if requested_model in SUPPORTED_MODELS:
        return requested_model
    # auto 或未知值 → 让 Orchestrator 决定
    return ""
