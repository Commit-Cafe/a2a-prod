"""P2.1 阶段 e2e 测试（SDLC WORKFLOW 端到端）。

前置条件：
- ``.env.prod`` 已填好三家 API Key
- 已运行 ``docker compose --env-file .env.prod up -d`` 启动完整栈
  （litellm + 3 Agent + orchestrator，挂载 workspace 卷）
- 全部容器 healthy

测试内容：
- 端到端：用户提研发需求 → 走完整 SDLC 工作流 → 返回拼接答案
- /v1/orchestrate/trace 返回完整 state（含 sdlc_doc 各阶段产出）
- spec.md / tech-design.md 落盘到 workspace/sdlc/<sid>/，工作流结束后文件仍在

标记：``@pytest.mark.e2e`` + ``@pytest.mark.p2_1_e2e``
（conftest 第 2 轮探活 orchestrator 已覆盖；不新增探活轮次）

用法::

    pytest -m "p2_1_e2e" tests/test_p2_1_e2e.py -v

注意：
- LLM 输出不可预测，断言只验证「路由/结构/落盘」，不验证 LLM 答案内容
- 一次完整工作流耗时 1-10 分钟（5+ 次 LLM 调用），timeout=600s
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from orchestrator.sdlc_workflow import (
    GLM_CODE_BLOCK_MAX_LINES,
    MAX_FEEDBACK_ROUNDS,
)

# ============================================================
# 辅助断言
# ============================================================


def _assert_glm_no_implementation(tech_design: str) -> None:
    """GLM 技术规范不应含完整实现（允许 ≤ 15 行的类型签名/伪代码）。"""
    for block in re.findall(r"```python\n(.*?)\n```", tech_design, re.DOTALL):
        lines = [ln for ln in block.split("\n") if ln.strip() and not ln.strip().startswith("#")]
        if len(lines) > GLM_CODE_BLOCK_MAX_LINES:
            pytest.fail(
                f"GLM tech_design 含超长代码块（{len(lines)} 行），疑似完整实现:\n{block[:200]}"
            )


def _assert_no_freestyle_in_final(implementation: str) -> None:
    """MiniMax 最终实现不应含自由发挥语义（应在反馈环节被纠正）。"""
    freestyle_markers = ["我额外", "我觉得应该", "我建议增加", "我自己加", "自行新增"]
    for marker in freestyle_markers:
        if marker in implementation:
            pytest.fail(f"MiniMax 最终实现含自由发挥语义 '{marker}'，未被反馈环节纠正")


# ============================================================
# Test 1：端到端 happy path（/v1/orchestrate 顶层）
# ============================================================


@pytest.mark.e2e
@pytest.mark.p2_1_e2e
def test_workflow_e2e_happy_path(orchestrator_url: str) -> None:
    """端到端：用户提研发需求 → 走完整 SDLC 工作流 → 返回拼接答案。"""
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate",
        json={
            "query": (
                "实现一个线程安全的 LRU 缓存，要求支持 TTL 过期，" "并附完整的 pytest 单元测试"
            )
        },
        timeout=600.0,
    )
    assert response.status_code == 200
    body = response.json()

    # 1. 路由正确（mode 字段就是 OrchestrationMode 值）
    assert body["mode"] == "workflow", f"expected workflow, got {body['mode']}"

    # 2. 必含字段
    assert body["session_id"]
    assert body["answer"]

    # 3. 最终答案含研发流程各阶段标题（aggregate 拼接产物，spec §5.6）
    answer = body["answer"]
    assert "📋" in answer or "Spec" in answer
    assert "🏗️" in answer or "技术规范" in answer
    assert "✅" in answer or "实现" in answer


# ============================================================
# Test 2：trace 端点返回完整 state（含 sdlc_doc 各阶段）
# ============================================================


@pytest.mark.e2e
@pytest.mark.p2_1_e2e
def test_workflow_e2e_trace_full_state(orchestrator_url: str) -> None:
    """/v1/orchestrate/trace 返回完整 state（含 sdlc_doc 各阶段产出）。"""
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate/trace",
        json={"query": "实现一个计算斐波那契数列的函数并附 pytest 测试"},
        timeout=600.0,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "workflow"

    full_state = body.get("full_state", {})
    assert "sdlc_doc" in full_state
    doc = full_state["sdlc_doc"]
    assert doc.get("spec"), "DeepSeek 未产出 spec"
    assert doc.get("tech_design"), "GLM 未产出 tech_design"
    assert doc.get("implementation"), "MiniMax 未产出 implementation"

    # 角色边界（GLM 不写实现代码）
    _assert_glm_no_implementation(doc["tech_design"])

    # MiniMax 严格遵循（无自由发挥语义）
    _assert_no_freestyle_in_final(doc["implementation"])

    # 反馈轮数合规（≤ MAX_FEEDBACK_ROUNDS）
    rounds = full_state.get("feedback_rounds", 0)
    assert rounds <= MAX_FEEDBACK_ROUNDS, f"feedback_rounds {rounds} > {MAX_FEEDBACK_ROUNDS}"

    # code_paths 基准一致（相对 workspace 根：sdlc/<sid>/code/xxx）
    sid = body["session_id"]
    code_paths = doc.get("code_paths", [])
    if code_paths:  # MiniMax 可能未真落盘（LLM 不确定性），有则校验基准
        assert all(
            p.startswith(f"sdlc/{sid}/code/") for p in code_paths
        ), f"code_paths 基准不一致: {code_paths}"


# ============================================================
# Test 3：落盘文件持久化
# ============================================================


@pytest.mark.e2e
@pytest.mark.p2_1_e2e
def test_workflow_e2e_workspace_files_persisted(orchestrator_url: str) -> None:
    """spec.md / tech-design.md 落盘到 workspace/sdlc/<sid>/，工作流结束后文件仍在。

    orchestrator 容器挂载了 workspace 卷；测试从主机端访问同一路径
    （docker-compose 把 ./workspace 挂载到所有容器）。
    """
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate",
        json={"query": "实现一个返回两个数之和的 add 函数并附 pytest 测试"},
        timeout=600.0,
    )
    assert response.status_code == 200
    sid = response.json()["session_id"]

    workspace_dir = Path("workspace") / "sdlc" / sid
    assert (workspace_dir / "spec.md").exists(), f"spec.md 未落盘: {workspace_dir}"
    assert (workspace_dir / "tech-design.md").exists(), f"tech-design.md 未落盘: {workspace_dir}"
    assert (workspace_dir / "spec.md").stat().st_size > 0, "spec.md 内容为空"
