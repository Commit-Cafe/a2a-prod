"""Aggregator 契约测试（SPEC §P2 聚合器）。

校验内容：
1. 单 Agent 响应：透传（不拼接）
2. 多 Agent 响应：按 GLM → DeepSeek → MiniMax 固定顺序拼接 markdown
3. 错误附录：errors 列表追加在末尾
4. 边界：空 responses / 单响应 + errors（场景 2 路径）

设计原则：
- 不依赖 LangGraph，直接调用 aggregate 函数
- 校验拼接顺序（关键：GLM 必须出现在 DeepSeek 之前）
"""

from __future__ import annotations

import pytest

from orchestrator.aggregator import aggregate
from orchestrator.state import OrchestrationState

# ============================================================
# 辅助：构造 state
# ============================================================


def _state(
    responses: dict[str, str] | None = None,
    errors: list[str] | None = None,
) -> OrchestrationState:
    state: OrchestrationState = {"session_id": "test-session"}
    if responses is not None:
        state["agent_responses"] = responses
    if errors is not None:
        state["errors"] = errors
    return state


# ============================================================
# Test 1：单 Agent 透传
# ============================================================


class TestPassthrough:
    """单 Agent 响应（DIRECT 模式典型场景）：直接透传。"""

    def test_single_response_passthrough(self) -> None:
        result = aggregate(_state(responses={"glm-agent": "hello"}))
        assert result["final_answer"] == "hello"

    def test_single_response_minimax_passthrough(self) -> None:
        result = aggregate(_state(responses={"minimax-agent": "code answer"}))
        assert result["final_answer"] == "code answer"

    def test_empty_responses_returns_default(self) -> None:
        result = aggregate(_state(responses={}))
        assert result["final_answer"] == "(无 Agent 响应)"

    def test_no_responses_key_returns_default(self) -> None:
        result = aggregate(_state())
        assert result["final_answer"] == "(无 Agent 响应)"


# ============================================================
# Test 2：多 Agent 拼接（DECOMPOSITION 模式）
# ============================================================


class TestMultiAgentMerge:
    """多 Agent 响应：按固定顺序拼接，加二级标题。"""

    def test_three_agents_in_fixed_order(self) -> None:
        """三个 Agent 都响应时，输出顺序：GLM → DeepSeek → MiniMax。"""
        result = aggregate(
            _state(
                responses={
                    "minimax-agent": "code-content",  # 故意打乱输入顺序
                    "glm-agent": "general-content",
                    "deepseek-agent": "logic-content",
                }
            )
        )
        final = result["final_answer"]
        # 校验顺序：GLM 必须出现在 DeepSeek 之前，DeepSeek 在 MiniMax 之前
        pos_glm = final.find("general-content")
        pos_deepseek = final.find("logic-content")
        pos_minimax = final.find("code-content")
        assert 0 <= pos_glm < pos_deepseek < pos_minimax

    def test_two_agents_partial(self) -> None:
        """仅两个 Agent 响应：按顺序拼接（缺中间一个也保持顺序）。"""
        result = aggregate(
            _state(
                responses={
                    "glm-agent": "general-content",
                    "minimax-agent": "code-content",
                }
            )
        )
        final = result["final_answer"]
        assert "general-content" in final
        assert "code-content" in final
        # GLM 必须在 MiniMax 之前
        assert final.find("general-content") < final.find("code-content")

    def test_section_headers_present(self) -> None:
        """拼接后每个 Agent 段都有二级标题（## 开头）。"""
        result = aggregate(
            _state(
                responses={
                    "glm-agent": "general",
                    "deepseek-agent": "logic",
                    "minimax-agent": "code",
                }
            )
        )
        final = result["final_answer"]
        assert "## GLM（代码审查）" in final
        assert "## DeepSeek（需求/方案）" in final
        assert "## MiniMax（代码实现）" in final

    def test_sections_separated_by_divider(self) -> None:
        """段与段之间用 ``---`` 分隔（markdown 水平线）。"""
        result = aggregate(
            _state(
                responses={
                    "glm-agent": "g",
                    "minimax-agent": "m",
                }
            )
        )
        assert "\n\n---\n\n" in result["final_answer"]

    def test_unknown_agent_appended_after_known(self) -> None:
        """不在 _AGENT_ORDER 中的 Agent 追加在末尾（兜底）。"""
        result = aggregate(
            _state(
                responses={
                    "glm-agent": "g",
                    "unknown-agent": "u",
                }
            )
        )
        final = result["final_answer"]
        assert final.find("g") < final.find("u")
        assert "## unknown-agent" in final


# ============================================================
# Test 3：错误附录
# ============================================================


class TestErrorAppendix:
    """errors 列表追加在最终答案末尾。"""

    def test_errors_appended_at_end(self) -> None:
        result = aggregate(
            _state(
                responses={
                    "glm-agent": "ok",
                    "deepseek-agent": "ok2",
                },
                errors=["minimax timeout", "internal error"],
            )
        )
        final = result["final_answer"]
        assert "## ⚠️ 执行错误" in final
        assert "minimax timeout" in final
        assert "internal error" in final
        # 错误附录在末尾
        assert final.rfind("internal error") > final.rfind("ok2")

    def test_single_response_with_errors_uses_markdown(self) -> None:
        """单响应 + errors 时走多 Agent 路径（因为 errors 非空）。

        注：aggregate 判断 ``len(responses) <= 1 and not errors`` 才走透传。
        """
        result = aggregate(
            _state(
                responses={"glm-agent": "ok"},
                errors=["some error"],
            )
        )
        final = result["final_answer"]
        assert "## GLM（代码审查）" in final
        assert "## ⚠️ 执行错误" in final

    def test_only_errors_no_responses(self) -> None:
        """全失败场景：仅 errors，无 responses。"""
        result = aggregate(
            _state(
                responses={},
                errors=["all agents failed"],
            )
        )
        final = result["final_answer"]
        assert "## ⚠️ 执行错误" in final
        assert "all agents failed" in final


# ============================================================
# Test 4：边界与异常
# ============================================================


class TestEdgeCases:
    """边界条件。"""

    def test_none_responses_treated_as_empty(self) -> None:
        """``state.get("agent_responses")`` 返回 None 时不报错。

        TypedDict 不允许 value 为 None，因此用 monkeypatch 把 responses 注入为 None。
        """
        # 直接构造 dict 然后通过 cast 跳过类型检查（运行时验证）
        state: OrchestrationState = {"session_id": "x"}
        # 把 agent_responses 显式置为 None：用 getattr 绕过类型系统
        state.update({"agent_responses": None})  # type: ignore[typeddict-item]
        result = aggregate(state)
        assert result["final_answer"] == "(无 Agent 响应)"

    def test_none_errors_treated_as_empty(self) -> None:
        state: OrchestrationState = {
            "session_id": "x",
            "agent_responses": {"glm-agent": "ok"},
        }
        state.update({"errors": None})  # type: ignore[typeddict-item]
        result = aggregate(state)
        assert result["final_answer"] == "ok"

    @pytest.mark.parametrize(
        "text",
        ["", "short", "a" * 1000, "包含\n换行\n的文本", "emoji 🎉"],
    )
    def test_various_text_content(self, text: str) -> None:
        result = aggregate(_state(responses={"glm-agent": text}))
        assert result["final_answer"] == text


# ============================================================
# Test 5：WORKFLOW 模式聚合（P2.1，spec §5.6）
# ============================================================


class TestWorkflowAggregation:
    """WORKFLOW 模式专属聚合（_aggregate_workflow）。"""

    def _wf_state(
        self,
        *,
        spec: str = "# Spec",
        tech_design: str = "# Tech",
        implementation: str = "# Impl",
        code_paths: list[str] | None = None,
        feedbacks: list[dict[str, object]] | None = None,
        rounds: int = 0,
        errors: list[str] | None = None,
    ) -> OrchestrationState:
        from orchestrator.state import OrchestrationMode

        state: OrchestrationState = {
            "session_id": "s1",
            "mode": OrchestrationMode.WORKFLOW.value,
            "sdlc_doc": {
                "spec": spec,
                "tech_design": tech_design,
                "implementation": implementation,
                "code_paths": code_paths or [],
            },
            "sdlc_feedback": feedbacks or [],  # type: ignore[arg-type]
            "feedback_rounds": rounds,
            "workflow_status": "running",
        }
        if errors is not None:
            state["errors"] = errors
        return state

    def test_workflow_aggregation_contains_all_sections(self) -> None:
        result = aggregate(self._wf_state(code_paths=["sdlc/s1/code/lru.py"]))
        final = result["final_answer"]
        assert "📋" in final or "Spec" in final
        assert "🏗️" in final or "技术规范" in final
        assert "✅" in final or "实现" in final
        assert "📁" in final
        assert "sdlc/s1/code/lru.py" in final
        assert "📊" in final  # 工作流状态

    def test_workflow_aggregation_includes_feedback(self) -> None:
        feedbacks = [{"round": 1, "blocker": "阻塞 X", "guidance": "指导 Y"}]
        result = aggregate(self._wf_state(feedbacks=feedbacks, rounds=1))
        assert "🔁" in result["final_answer"]
        assert "阻塞 X" in result["final_answer"]
        assert "指导 Y" in result["final_answer"]

    def test_workflow_unresolved_marks_status(self) -> None:
        """达上限仍有 NEED_HELP → status=blocked_unresolved。"""
        from orchestrator.sdlc_workflow import MAX_FEEDBACK_ROUNDS, NEED_HELP_MARKER

        result = aggregate(
            self._wf_state(
                implementation=f"{NEED_HELP_MARKER} 未解决",
                rounds=MAX_FEEDBACK_ROUNDS,
            )
        )
        assert result["workflow_status"] == "blocked_unresolved"
        assert "需人工介入" in result["final_answer"]

    def test_workflow_resolved_status(self) -> None:
        """有反馈但最终无 NEED_HELP → status=blocked_resolved。"""
        result = aggregate(self._wf_state(rounds=1))
        assert result["workflow_status"] == "blocked_resolved"

    def test_workflow_no_feedback_status(self) -> None:
        """无反馈 → blocked_resolved（一次通过）。"""
        result = aggregate(self._wf_state(rounds=0))
        assert result["workflow_status"] == "blocked_resolved"
        assert "一次通过" in result["final_answer"]
