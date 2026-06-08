"""Orchestrator FastAPI Host（端口 12080）。

入口：
    python -m orchestrator            # 启动 uvicorn
    python -m orchestrator --reload   # 开发模式（自动重载）

API：
    POST /v1/orchestrate       - 同步编排（A2A-native 入口，P2 引入）
    POST /v1/orchestrate/trace - 同步编排，返回完整 state（含中间步骤）
    POST /v1/chat/completions  - OpenAI 兼容 chat completions（P5 引入）
    POST /v1/chat/completions/stream - OpenAI 兼容流式 chat completions（P5 引入）
    GET  /v1/models            - OpenAI 兼容 models 列表（P5 引入）
    GET  /health               - 健康检查
    GET  /                     - 服务信息

设计原则：
- **SPEC §3.6**：依赖 LiteLLM（healthcheck 通过）+ 三 Agent（healthcheck 通过）才启动
- **SPEC §3.3**：非 root 运行（docker 内）+ HEALTHCHECK
- **SPEC §1.5**：错误码映射（业务异常 → HTTP 状态码）
- **SPEC §3.9**：OpenAI 兼容层独立模块，路由到相同的 ``orchestrate()`` 内核
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from observability.tracing import trace_node
from orchestrator import a2a_client, openai_compat
from orchestrator.graph import build_graph, orchestrate
from orchestrator.openai_compat import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    DeltaContent,
    ModelListResponse,
    StreamChunk,
    StreamChoice,
)

logger = structlog.get_logger(__name__)


# ============================================================
# Request / Response Schemas（A2A-native）
# ============================================================


class OrchestrateRequest(BaseModel):
    """编排请求。"""

    query: str = Field(..., min_length=1, max_length=4000, description="用户原始问题")
    session_id: str | None = Field(None, description="会话 ID（可选，用于日志关联）")
    user_id: str | None = Field(None, description="业务用户 ID（透传到 Langfuse trace）")
    request_id: str | None = Field(None, description="调用方 request_id（透传到 trace）")


class OrchestrateResponse(BaseModel):
    """编排响应（精简版，给最终用户）。"""

    answer: str = Field(..., description="最终答案")
    mode: str = Field(..., description="编排模式（direct / task_decomposition）")
    agent_responses: dict[str, str] = Field(
        default_factory=dict,
        description="各 Agent 的原始响应（agent_name → text）",
    )
    errors: list[str] = Field(default_factory=list, description="执行中的非致命错误")
    session_id: str = Field(..., description="会话 ID（请求时未传则自动生成）")


class TraceResponse(OrchestrateResponse):
    """编排响应（含完整 state，用于调试）。"""

    full_state: dict[str, Any] = Field(..., description="完整 LangGraph state")


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str = "ok"
    service: str = "orchestrator"
    version: str = "0.1.0"


# ============================================================
# P5: API Key 鉴权（OpenAI 兼容层与原 a2a 端点共用）
# ============================================================


def _expected_api_key() -> str | None:
    """从环境变量取期望的 API key；None 表示不校验（开发模式）。"""
    return os.getenv("ORCHESTRATOR_API_KEY") or os.getenv("LITELLM_MASTER_KEY")


def verify_api_key(  # noqa: D401 - 简单 inline 注释
    request: Request,
) -> None:
    """FastAPI 依赖：校验 ``Authorization: Bearer <key>``。

    Open WebUI 端设置 ``OpenAI API Key`` 字段时，会带
    ``Authorization: Bearer <key>`` 头。本函数校验 key 与 ``LITELLM_MASTER_KEY``
    或 ``ORCHESTRATOR_API_KEY`` 一致；不通过则 401。

    注意：本函数 MUST NOT 区分大小写比较（HTTP 头值大小写无关但 key 区分）。
    """
    expected = _expected_api_key()
    if expected is None or expected == "":
        # 未配置 key → 跳过鉴权（开发模式 / e2e 测试场景）
        return

    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    provided = auth[len("Bearer ") :].strip()
    if provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid api key",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ============================================================
# FastAPI lifespan（启动/关闭钩子）
# ============================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001 - FastAPI 约定签名
    """启动时预热图、关闭时释放 httpx 连接池。"""
    # P4: 启动时注入 trace（失败不阻塞 Orchestrator 启动）
    try:
        from observability.setup import setup_agent

        setup_agent()
    except Exception as e:  # noqa: BLE001
        logger.warning("setup_agent_failed_continue", error=str(e))

    # 启动：构建图（预热）
    logger.info("orchestrator_starting", port=os.getenv("PORT", "12080"))
    build_graph()  # 触发 compile，发现配置错误时立即 fail-fast
    logger.info("orchestrator_graph_built")

    yield

    # 关闭：释放 httpx 连接池
    await a2a_client.close_client()
    logger.info("orchestrator_stopped")


# ============================================================
# FastAPI app
# ============================================================


def create_app() -> FastAPI:
    """FastAPI 工厂。"""
    app = FastAPI(
        title="a2a-prod Orchestrator",
        description="LangGraph-based multi-agent orchestrator (A2A + OpenAI compat)",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ---- 健康检查 ----
    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/", response_model=HealthResponse)
    async def root() -> HealthResponse:
        return HealthResponse()

    # ---- 编排：同步（A2A-native） ----
    @app.post("/v1/orchestrate", response_model=OrchestrateResponse)
    @trace_node(name="orchestrator.endpoint.orchestrate")
    async def orchestrate_endpoint(req: OrchestrateRequest) -> OrchestrateResponse:
        try:
            state = await orchestrate(
                req.query,
                session_id=req.session_id,
                user_id=req.user_id,
            )
        except Exception as e:
            # 兜底：未预期异常（SPEC §1.5 → HTTP 500）
            logger.exception("orchestrate_unexpected_error")
            raise HTTPException(
                status_code=500,
                detail=f"orchestrate failed: {type(e).__name__}: {e}",
            ) from e

        return OrchestrateResponse(
            answer=state.get("final_answer", ""),
            mode=state.get("mode", ""),
            agent_responses=state.get("agent_responses", {}),
            errors=state.get("errors", []),
            session_id=state.get("session_id", ""),
        )

    # ---- 编排：调试模式（返回完整 state） ----
    @app.post("/v1/orchestrate/trace", response_model=TraceResponse)
    @trace_node(name="orchestrator.endpoint.orchestrate_trace")
    async def orchestrate_trace_endpoint(req: OrchestrateRequest) -> TraceResponse:
        try:
            state = await orchestrate(
                req.query,
                session_id=req.session_id,
                user_id=req.user_id,
            )
        except Exception as e:
            logger.exception("orchestrate_trace_unexpected_error")
            raise HTTPException(
                status_code=500,
                detail=f"orchestrate failed: {type(e).__name__}: {e}",
            ) from e

        # full_state 不能含非可序列化对象，但我们的 state 都是 JSON-safe
        serializable_state = {
            k: v
            for k, v in state.items()
            if k != "messages"  # 排除 LangGraph messages reducer
        }
        return TraceResponse(
            answer=state.get("final_answer", ""),
            mode=state.get("mode", ""),
            agent_responses=state.get("agent_responses", {}),
            errors=state.get("errors", []),
            session_id=state.get("session_id", ""),
            full_state=serializable_state,
        )

    # ============================================================
    # P5: OpenAI 兼容端点
    # ============================================================

    @app.get("/v1/models", response_model=ModelListResponse)
    @trace_node(name="orchestrator.endpoint.list_models")
    async def list_models_endpoint(
        _auth: None = Depends(verify_api_key),
    ) -> ModelListResponse:
        """OpenAI 兼容 ``GET /v1/models`` —— 列出 3 个可路由 Agent。"""
        return openai_compat.list_models()

    @app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
    @trace_node(name="orchestrator.endpoint.chat_completions")
    async def chat_completions_endpoint(
        req: ChatCompletionRequest,
        _auth: None = Depends(verify_api_key),
    ) -> ChatCompletionResponse | StreamingResponse:
        """OpenAI 兼容 ``POST /v1/chat/completions``。

        处理流程：
        1. 校验 ``model`` 字段（SPEC §3.9.3）：
           - 3 个具体 Agent 名 → 强制 DIRECT 路由
           - ``auto`` 或未知值 → 走 Orchestrator 自主分类
        2. 把 messages 转 user_query（取最后一条 user，前置所有 system）
        3. 调 ``orchestrate()`` 拿 state
        4. 把 state 转 OpenAI 响应
        5. ``stream=true`` 时改用 SSE 协议
        """
        # 1. 校验 model 字段
        if not openai_compat.is_supported_model(req.model):
            # 兜底：未知 model → auto（Open WebUI 偶尔塞自定义名）
            logger.warning("openai_compat_unknown_model_fallback", model=req.model)
            effective_model = openai_compat.DEFAULT_MODEL
        else:
            effective_model = req.model

        # 2. messages → query
        user_query = openai_compat.extract_user_query(req.messages)
        if not user_query:
            raise HTTPException(
                status_code=400,
                detail="messages must contain at least one 'user' role message",
            )

        # 3. model → target_agent（auto / 未知 → 空串让 Orchestrator 自由路由）
        target_agent = openai_compat.resolve_target_agent(effective_model)

        # 4. 流式分支
        if req.stream:
            return _stream_chat_completions(
                user_query=user_query,
                effective_model=effective_model,
                target_agent=target_agent,
            )

        # 5. 同步分支
        try:
            state = await orchestrate(
                user_query,
                session_id=None,
                user_id=None,
            )
        except Exception as e:
            logger.exception("openai_compat_unexpected_error")
            raise HTTPException(
                status_code=500,
                detail=f"chat completion failed: {type(e).__name__}: {e}",
            ) from e

        # 若用户强制指定 Agent，则把 target_agent 注入 state（让响应 model 字段准确）
        response_model = target_agent or state.get("target_agent") or effective_model

        return openai_compat.build_chat_completion_response(
            state=state,
            model=response_model,
        )

    return app


# ============================================================
# OpenAI 兼容流式响应（SSE 协议）
# ============================================================


async def _stream_chat_completions(
    *,
    user_query: str,
    effective_model: str,
    target_agent: str,
) -> StreamingResponse:
    """SSE 格式的流式 chat completions。

    OpenAI 流式响应格式（每行以 ``data:`` 开头，最后以 ``data: [DONE]`` 收尾）：

    .. code-block:: text

        data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk",...,"choices":[{"index":0,"delta":{"content":"你"},"finish_reason":null}]}

        data: {"id":"chatcmpl-xxx",...,"choices":[{"index":0,"delta":{"content":"好"},"finish_reason":null}]}

        ...

        data: [DONE]

    本阶段策略：一次性跑完 orchestrate()，拿到 final_answer，再按 20 字符一块
    流式推送给客户端（模拟 streaming）。P5.1 升级到真正 incremental（SPEC §3.9.4）。
    """
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created_ts = int(time.time())

    async def event_source() -> AsyncIterator[str]:
        try:
            state = await orchestrate(
                user_query,
                session_id=None,
                user_id=None,
            )
        except Exception as e:  # noqa: BLE001
            err_chunk = StreamChunk(
                id=chunk_id,
                created=created_ts,
                model=target_agent or effective_model,
                choices=[
                    StreamChoice(
                        index=0,
                        delta=DeltaContent(content=f"\n\n[error] {type(e).__name__}: {e}"),
                        finish_reason="stop",
                    ),
                ],
            )
            yield f"data: {err_chunk.model_dump_json()}\n\n"
            yield "data: [DONE]\n\n"
            return

        response_model = target_agent or state.get("target_agent") or effective_model
        final_answer = str(state.get("final_answer", ""))

        # 第一块：role=assistant 标识
        first_chunk = StreamChunk(
            id=chunk_id,
            created=created_ts,
            model=response_model,
            choices=[
                StreamChoice(
                    index=0,
                    delta=DeltaContent(),
                    finish_reason=None,
                ),
            ],
        )
        yield f"data: {first_chunk.model_dump_json()}\n\n"

        # 中间块：每 20 字符一段
        chunk_size = 20
        for i in range(0, len(final_answer), chunk_size):
            piece = final_answer[i : i + chunk_size]
            chunk = StreamChunk(
                id=chunk_id,
                created=created_ts,
                model=response_model,
                choices=[
                    StreamChoice(
                        index=0,
                        delta=DeltaContent(content=piece),
                        finish_reason=None,
                    ),
                ],
            )
            yield f"data: {chunk.model_dump_json()}\n\n"

        # 结束块：finish_reason=stop
        end_chunk = StreamChunk(
            id=chunk_id,
            created=created_ts,
            model=response_model,
            choices=[
                StreamChoice(
                    index=0,
                    delta=DeltaContent(),
                    finish_reason="stop",
                ),
            ],
        )
        yield f"data: {end_chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# CLI 入口
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="orchestrator",
        description="a2a-prod Orchestrator (LangGraph + FastAPI + OpenAI compat)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("HOST", "0.0.0.0"),
        help="监听地址（默认 0.0.0.0）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "12080")),
        help="监听端口（默认 12080，SPEC §3.1）",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="开发模式（自动重载）",
    )
    args = parser.parse_args()

    app = create_app()

    # 注：lifespan 通过 uvicorn 触发，不用 asyncio.run
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
