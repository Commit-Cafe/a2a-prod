"""Executor 节点：调用下游 A2A Agent 执行任务。

两种执行模式：
1. **direct_execute**：单 Agent 调用（对应 DIRECT 模式）
2. **decompose_execute**：多 Agent 并行调用（对应 TASK_DECOMPOSITION 模式）

设计原则：
- **协议优先**：始终走 A2A JSON-RPC（用户决策 A）
- **错误隔离**：单个 Agent 失败不阻塞整图，错误进 ``state["errors"]``
- **并行调用**：DECOMPOSITION 模式用 asyncio.gather 并行
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import structlog

from observability.tracing import trace_node
from orchestrator import a2a_client
from orchestrator.state import OrchestrationState, SubTask

logger = structlog.get_logger(__name__)


# ============================================================
# Agent URL 配置（SPEC §3.1 端口表）
# ============================================================


# S2 修复（GLM 2026-06-18 review）：默认 URL 不再硬编码 docker 容器名（a2a-prod-glm-agent），
# 改用 compose service name（glm-agent）+ 容器内端口 8000。这种形态：
#   - docker-compose 下：service name 解析为 compose network 内容器名（容器名是 a2a-prod-glm-agent，
#     但 docker 网络的 DNS 也支持短 service name）→ 行为兼容
#   - K8s 下：Service metadata.name 正是 glm-agent（20-agents.yaml）→ 同样解析
# 部署到生产环境时，强烈建议显式注入 AGENT_URLS_GLM_AGENT 等 env 覆盖（避免 DNS 解析差异），
# 未设置时 logger.warning 提醒。
_DEFAULT_AGENT_URLS: dict[str, str] = {
    "glm-agent": os.getenv("AGENT_URLS_GLM_AGENT") or "http://glm-agent:8000",
    "deepseek-agent": os.getenv("AGENT_URLS_DEEPSEEK_AGENT") or "http://deepseek-agent:8000",
    "minimax-agent": os.getenv("AGENT_URLS_MINIMAX_AGENT") or "http://minimax-agent:8000",
}

# 启动时若所有 URL 都走默认（说明 env 未注入），一次性打 warning（避免每个请求刷屏）
if not any(
    os.getenv(k)
    for k in (
        "AGENT_URLS_GLM_AGENT",
        "AGENT_URLS_DEEPSEEK_AGENT",
        "AGENT_URLS_MINIMAX_AGENT",
    )
):
    logger.warning(
        "agent_urls_using_default",
        hint="生产环境建议显式注入 AGENT_URLS_GLM_AGENT / DEEPSEEK / MINIMAX env",
    )


def get_agent_url(agent_name: str, *, override: dict[str, str] | None = None) -> str:
    """根据 Agent 名取 URL。

    Args:
        agent_name: AgentName 的 value（如 "glm-agent"）
        override: 覆盖默认 URL（e2e 测试时用 localhost:port）
    """
    if override and agent_name in override:
        return override[agent_name]
    if agent_name not in _DEFAULT_AGENT_URLS:
        raise ValueError(f"unknown agent: {agent_name}; valid: {list(_DEFAULT_AGENT_URLS)}")
    return _DEFAULT_AGENT_URLS[agent_name]


# ============================================================
# DIRECT 模式：单 Agent 调用
# ============================================================


async def direct_execute(
    state: OrchestrationState,
) -> dict[str, Any]:
    """LangGraph 节点：单 Agent 执行。

    输入：state["target_agent"] + state["user_query"]
    输出：partial state ``{"agent_responses": {agent_name: text}}``
    """
    agent_name = state["target_agent"]
    query = state["user_query"]
    session_id = state.get("session_id", "-")
    raw_overrides = state.get("_test_overrides")
    override_urls: dict[str, str] | None = None
    if isinstance(raw_overrides, dict):
        agent_urls = raw_overrides.get("agent_urls")
        if isinstance(agent_urls, dict):
            override_urls = agent_urls

    log = logger.bind(session_id=session_id, agent=agent_name, mode="direct")

    url = get_agent_url(agent_name, override=override_urls)
    raw_user_id = state.get("user_id")
    user_id = str(raw_user_id) if isinstance(raw_user_id, str) else None
    try:
        text = await a2a_client.message_send(url, query, session_id=session_id, user_id=user_id)
    except a2a_client.A2AClientError as e:
        await log.aerror("direct_execute_failed", error=str(e)[:200])
        return {
            "agent_responses": {},
            "errors": [f"direct_execute[{agent_name}]: {type(e).__name__}: {e}"],
        }

    await log.ainfo("direct_execute_ok", text_preview=text[:60])
    return {
        "agent_responses": {agent_name: text},
    }


direct_execute = trace_node(name="orchestrator.direct_execute")(direct_execute)


# ============================================================
# TASK_DECOMPOSITION 模式：多 Agent 并行
# ============================================================


async def decompose_execute(
    state: OrchestrationState,
) -> dict[str, Any]:
    """LangGraph 节点：多 Agent 并行执行（asyncio.gather）。

    输入：state["subtasks"] = [{description, assigned_to}, ...]
    输出：partial state ``{"agent_responses": {agent_name: text}}``
    """
    subtasks: list[SubTask] = state.get("subtasks", [])
    session_id = state.get("session_id", "-")
    raw_overrides = state.get("_test_overrides")
    override_urls: dict[str, str] | None = None
    if isinstance(raw_overrides, dict):
        agent_urls = raw_overrides.get("agent_urls")
        if isinstance(agent_urls, dict):
            override_urls = agent_urls

    log = logger.bind(session_id=session_id, mode="decompose", subtask_count=len(subtasks))

    if not subtasks:
        await log.awarning("decompose_no_subtasks")
        return {"agent_responses": {}, "errors": ["decompose_execute: no subtasks"]}

    # 并行调度
    tasks = [_invoke_single(subtask, override_urls, session_id) for subtask in subtasks]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 合并结果
    agent_responses: dict[str, str] = {}
    errors: list[str] = []
    for subtask, result in zip(subtasks, results, strict=True):
        agent_name = subtask.get("assigned_to", "?")
        if isinstance(result, BaseException):
            errors.append(f"decompose_execute[{agent_name}]: {type(result).__name__}: {result}")
        elif isinstance(result, dict):
            agent_responses.update(result)
        else:
            errors.append(f"decompose_execute[{agent_name}]: unexpected result type {type(result)}")

    await log.ainfo(
        "decompose_execute_done",
        ok_count=len(agent_responses),
        error_count=len(errors),
    )
    return {"agent_responses": agent_responses, "errors": errors}


decompose_execute = trace_node(name="orchestrator.decompose_execute")(decompose_execute)


async def _invoke_single(
    subtask: SubTask,
    override_urls: dict[str, str] | None,
    session_id: str,
) -> dict[str, str]:
    """调用单个子任务。返回 ``{agent_name: text}``，异常向上抛。"""
    agent_name = subtask["assigned_to"]
    description = subtask["description"]

    url = get_agent_url(agent_name, override=override_urls)
    log = logger.bind(session_id=session_id, agent=agent_name, subtask_preview=description[:40])

    try:
        text = await a2a_client.message_send(url, description, session_id=session_id)
    except a2a_client.A2AClientError as e:
        await log.aerror("subtask_failed", error=str(e)[:200])
        raise
    await log.ainfo("subtask_ok", text_preview=text[:60])
    return {agent_name: text}
