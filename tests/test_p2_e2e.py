"""P2 阶段 e2e 测试（Orchestrator HTTP 端点）。

前置条件：
- ``.env.prod`` 已填好三家 API Key
- 已运行 ``docker compose --env-file .env.prod up -d`` 启动 litellm + 3 Agent + orchestrator
- 五个容器全部 healthy（用 ``docker compose ps`` 检查）

测试内容：
- ``GET /health`` 健康检查
- ``POST /v1/orchestrate`` DIRECT 模式（3 类问题 × 3 个 Agent 路由）
- ``POST /v1/orchestrate`` DECOMPOSITION 模式（对比类问题）
- ``POST /v1/orchestrate/trace`` 返回 full_state
- ``POST /v1/orchestrate`` 错误请求（空 query → 422）

标记：``@pytest.mark.e2e`` + ``@pytest.mark.p2_e2e``
- ``e2e``：conftest 第 1 轮探活 GLM Agent
- ``p2_e2e``：conftest 第 2 轮探活 Orchestrator

用法：
    # 仅跑 P2 e2e
    pytest -m "p2_e2e" tests/test_p2_e2e.py

    # 全跑（含 P1 e2e）
    pytest -m "e2e"
"""

from __future__ import annotations

import httpx
import pytest

# ============================================================
# 健康检查
# ============================================================


@pytest.mark.e2e
@pytest.mark.p2_e2e
def test_health_endpoint(orchestrator_url: str) -> None:
    """SPEC §3.3：``/health`` MUST 返回 200 + status=ok。"""
    response = httpx.get(f"{orchestrator_url}/health", timeout=10.0)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "orchestrator"


@pytest.mark.e2e
@pytest.mark.p2_e2e
def test_root_endpoint_returns_health(orchestrator_url: str) -> None:
    """``/`` 也返回健康响应（与 ``/health`` 一致）。"""
    response = httpx.get(f"{orchestrator_url}/", timeout=10.0)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


# ============================================================
# DIRECT 模式：3 类问题路由到不同 Agent
# ============================================================


@pytest.mark.e2e
@pytest.mark.p2_e2e
def test_direct_mode_requirement_routes_to_deepseek(orchestrator_url: str) -> None:
    """PM/CTO 类问题（技术选型）→ DIRECT → deepseek-agent。"""
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate",
        json={
            "query": "我们需要做技术选型，PostgreSQL 和 MongoDB 哪个更适合存用户画像？请给方案推荐和权衡分析"
        },
        timeout=180.0,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "direct"
    assert body["agent_responses"] != {}
    # DeepSeek 应该被选中（命中 "选型"/"方案"/"权衡"/"分析"）
    assert "deepseek-agent" in body["agent_responses"]
    assert body["errors"] == []
    assert isinstance(body["answer"], str) and body["answer"]
    assert body["session_id"]  # 自动生成


@pytest.mark.e2e
@pytest.mark.p2_e2e
def test_direct_mode_code_routes_to_minimax(orchestrator_url: str) -> None:
    """代码工程问题 → DIRECT → minimax-agent。"""
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate",
        json={"query": "帮我用 Python 写一个返回 hello 的函数"},
        timeout=180.0,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "direct"
    assert "minimax-agent" in body["agent_responses"]
    assert body["errors"] == []


@pytest.mark.e2e
@pytest.mark.p2_e2e
def test_direct_mode_general_routes_to_glm(orchestrator_url: str) -> None:
    """通用问题 → DIRECT → glm-agent。"""
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate",
        json={"query": "你好，请用一句话介绍量子计算"},
        timeout=180.0,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "direct"
    assert "glm-agent" in body["agent_responses"]


# ============================================================
# DECOMPOSITION 模式
# ============================================================


@pytest.mark.e2e
@pytest.mark.p2_e2e
def test_decomposition_mode_comparison_query(orchestrator_url: str) -> None:
    """对比类问题 → DECOMPOSITION → 多 Agent 并行。

    注意：此测试会真实调三个 LLM，耗时较长（30-90s）。
    """
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate",
        json={"query": "对比 Python 和 Go 的优缺点"},
        timeout=300.0,  # 多 Agent 并行，给充足时间
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "task_decomposition"
    # 至少两个 Agent 响应（允许部分失败）
    assert len(body["agent_responses"]) >= 2
    # 最终答案拼接（含 ## 二级标题）
    assert "##" in body["answer"]
    # session_id 自动生成
    assert body["session_id"].startswith("orch-")


# ============================================================
# trace 端点（返回完整 state）
# ============================================================


@pytest.mark.e2e
@pytest.mark.p2_e2e
def test_trace_endpoint_returns_full_state(orchestrator_url: str) -> None:
    """``/v1/orchestrate/trace`` MUST 返回 full_state 字段（含中间步骤）。"""
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate/trace",
        json={"query": "你好"},
        timeout=180.0,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "direct"
    assert "full_state" in body
    assert isinstance(body["full_state"], dict)
    # full_state 应该含 user_query / mode / target_agent / final_answer
    assert body["full_state"].get("user_query") == "你好"
    assert body["full_state"].get("mode") == "direct"


# ============================================================
# 错误处理
# ============================================================


@pytest.mark.e2e
@pytest.mark.p2_e2e
def test_empty_query_returns_422(orchestrator_url: str) -> None:
    """空 query 违反 min_length=1，pydantic 校验失败 → 422。"""
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate",
        json={"query": ""},
        timeout=10.0,
    )
    assert response.status_code == 422  # Unprocessable Entity


@pytest.mark.e2e
@pytest.mark.p2_e2e
def test_long_query_over_max_length_returns_422(orchestrator_url: str) -> None:
    """超过 max_length=4000 的 query → 422。"""
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate",
        json={"query": "a" * 4001},
        timeout=10.0,
    )
    assert response.status_code == 422
