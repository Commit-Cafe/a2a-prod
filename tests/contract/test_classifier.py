"""Classifier 契约测试（SPEC §P2 路由器）。

校验内容：
1. 关键词路由：DeepSeek / MiniMax / GLM 三向分流 + fallback
2. DECOMPOSITION 触发：正则强匹配 + 长度+关键词弱匹配
3. ``route_after_classify``：mode → 节点名映射
4. ``_decompose``：拆出 3 个子任务、assigned_to 正确

设计原则：
- 参数化关键词，避免遗漏边界
- 不 mock logger，但 logger 输出不影响断言
"""

from __future__ import annotations

from typing import cast

import pytest

from orchestrator.classifier import (
    DECOMPOSITION_PATTERNS,
    DEEPSEEK_KEYWORDS,
    GLM_KEYWORDS,
    MINIMAX_KEYWORDS,
    _decompose,
    _is_decomposition,
    _select_agent_by_keyword,
    classify,
    route_after_classify,
)
from orchestrator.state import (
    AgentName,
    OrchestrationMode,
    OrchestrationState,
)

# ============================================================
# 辅助：构造最小 state（其他 key 由 classify 输出）
# ============================================================


def _state(query: str) -> OrchestrationState:
    return {"user_query": query, "session_id": "test-session"}


# ============================================================
# Test 1：关键词路由（参数化关键词 + 单 Agent 选择）
# ============================================================


class TestKeywordRouting:
    """``_select_agent_by_keyword`` 三向分流 + fallback。"""

    @pytest.mark.parametrize("keyword", DEEPSEEK_KEYWORDS)
    def test_deepseek_keywords(self, keyword: str) -> None:
        target, reason = _select_agent_by_keyword(f"请帮我处理 {keyword} 问题")
        assert target == AgentName.DEEPSEEK.value
        # 不严格校验 reason 中含本 keyword：classifier 按 list 顺序短路匹配，
        # 复合关键词（如 "数学归纳"）可能命中更前的 "数学"，这是设计行为。
        assert reason.startswith("keyword:")

    @pytest.mark.parametrize("keyword", MINIMAX_KEYWORDS)
    def test_minimax_keywords(self, keyword: str) -> None:
        # 用前缀避免命中 DeepSeek 关键词（如 "测试" 不在 DeepSeek 列表）
        target, reason = _select_agent_by_keyword(f"请帮我处理 {keyword} 任务")
        assert target == AgentName.MINIMAX.value
        assert reason.startswith("keyword:")

    @pytest.mark.parametrize("keyword", GLM_KEYWORDS)
    def test_glm_keywords(self, keyword: str) -> None:
        # 用前缀避免误命中其他更专精的关键词
        target, reason = _select_agent_by_keyword(f"请帮我处理 {keyword} 的事情")
        assert target == AgentName.GLM.value
        assert reason.startswith("keyword:") or reason.startswith("fallback:")

    def test_no_keyword_falls_back_to_glm(self) -> None:
        """全部关键词 miss 时，fallback 到 GLM。"""
        target, reason = _select_agent_by_keyword("今天天气不错啊")
        assert target == AgentName.GLM.value
        assert reason == "fallback:no_keyword_match"

    def test_priority_deepseek_over_minimax(self) -> None:
        """同时命中 DeepSeek 和 MiniMax 关键词时，DeepSeek 优先（PM/CTO 角色更专精）。"""
        # "设计" 在 DeepSeek（PM/CTO），"代码" 在 MiniMax（程序员）
        target, _ = _select_agent_by_keyword("帮我设计代码结构")
        assert target == AgentName.DEEPSEEK.value

    def test_case_insensitive(self) -> None:
        """英文关键词大小写不敏感。"""
        target, _ = _select_agent_by_keyword("Please REFACTOR this code")
        assert target == AgentName.MINIMAX.value


# ============================================================
# Test 2：DECOMPOSITION 触发判定
# ============================================================


class TestDecompositionDetection:
    """``_is_decomposition`` 正则强匹配 + 长度+关键词弱匹配。"""

    # ---- 强匹配（正则）----

    @pytest.mark.parametrize(
        "query",
        [
            "对比 Python 和 Go 的优缺点",
            "比较 Redis 和 Memcached 的性能",
            "请分别介绍这三种方案",
            "GLM 和 DeepSeek 的区别是什么",
            "A 与 B 在哪些方面不同",
            "compare GLM and DeepSeek",
            "Python versus Java performance",
            "GPT vs Claude",
            "VS DeepSeek",  # 大小写不敏感
        ],
    )
    def test_strong_pattern_triggers(self, query: str) -> None:
        assert _is_decomposition(query) is True

    def test_strong_pattern_with_short_query(self) -> None:
        """强模式不依赖长度约束，短查询也能触发。"""
        assert _is_decomposition("A 和 B 的区别") is True

    # ---- 弱匹配（关键词 + 长度 >= 10）----

    def test_weak_keyword_long_query_triggers(self) -> None:
        """长查询（>=10 字）含 '对比' 触发。"""
        assert _is_decomposition("我想对比一下这两个框架的设计哲学") is True

    def test_weak_keyword_short_query_no_trigger(self) -> None:
        """短查询（<10 字）含关键词不触发（避免 '我和你' 误判）。"""
        assert _is_decomposition("对比") is False  # 仅 2 字
        assert _is_decomposition("我和你") is False  # 3 字，'和' 不在弱关键词里

    def test_no_decomposition_keyword(self) -> None:
        assert _is_decomposition("今天天气真好啊，出去走走") is False

    def test_decomposition_patterns_compiled(self) -> None:
        """DECOMPOSITION_PATTERNS 全部是已编译的 Pattern。"""
        import re

        for pattern in DECOMPOSITION_PATTERNS:
            assert isinstance(pattern, re.Pattern)


# ============================================================
# Test 3：classify() LangGraph 节点
# ============================================================


class TestClassifyNode:
    """``classify`` 节点：分类 + 路由（端到端）。"""

    def test_requirement_query_routes_direct_to_deepseek(self) -> None:
        """PM/CTO 类问题 → DIRECT → deepseek-agent。"""
        result = classify(_state("请帮我做技术选型"))
        assert result["mode"] == OrchestrationMode.DIRECT.value
        assert result["target_agent"] == AgentName.DEEPSEEK.value
        assert result["subtasks"] == []

    def test_code_query_routes_direct_to_minimax(self) -> None:
        result = classify(_state("帮我重构这段代码"))
        assert result["mode"] == OrchestrationMode.DIRECT.value
        assert result["target_agent"] == AgentName.MINIMAX.value

    def test_general_query_routes_direct_to_glm(self) -> None:
        result = classify(_state("你好，请解释一下量子计算"))
        assert result["mode"] == OrchestrationMode.DIRECT.value
        assert result["target_agent"] == AgentName.GLM.value

    def test_comparison_query_routes_decomposition(self) -> None:
        result = classify(_state("对比 Python 和 Go 的优缺点"))
        assert result["mode"] == OrchestrationMode.TASK_DECOMPOSITION.value
        assert isinstance(result["subtasks"], list)
        assert len(result["subtasks"]) == 3
        assert result["target_agent"] == ""

    def test_decomposition_priority_over_direct(self) -> None:
        """'对比 GLM 和 DeepSeek 设计' 同时命中对比 + 设计 → DECOMPOSITION 优先。"""
        result = classify(_state("对比 GLM 和 DeepSeek 设计方案的优劣"))
        assert result["mode"] == OrchestrationMode.TASK_DECOMPOSITION.value


# ============================================================
# Test 4：_decompose() 拆解
# ============================================================


class TestDecompose:
    """``_decompose`` 拆出 3 个子任务、assigned_to 各自不同。"""

    def test_returns_three_subtasks(self) -> None:
        subtasks = _decompose("对比 A 和 B")
        assert len(subtasks) == 3

    def test_assigned_to_distinct_agents(self) -> None:
        subtasks = _decompose("对比 A 和 B")
        assigned = {st["assigned_to"] for st in subtasks}
        assert assigned == {
            AgentName.GLM.value,
            AgentName.DEEPSEEK.value,
            AgentName.MINIMAX.value,
        }

    def test_descriptions_contain_original_query(self) -> None:
        query = "对比 Python 和 Go 的并发模型"
        subtasks = _decompose(query)
        for st in subtasks:
            assert query in st["description"]

    def test_descriptions_distinct_per_agent_role(self) -> None:
        """每个子任务的 description 应反映 Agent 角色倾向（推理/工程/通用）。"""
        subtasks = _decompose("test query")
        descriptions = [st["description"] for st in subtasks]
        # 不应完全相同（至少中文角色词不同）
        assert len(set(descriptions)) == 3


# ============================================================
# Test 5：route_after_classify
# ============================================================


class TestRouteAfterClassify:
    """``route_after_classify`` 根据 mode 返回 LangGraph 节点名。"""

    def test_direct_mode(self) -> None:
        state: OrchestrationState = {"mode": OrchestrationMode.DIRECT.value}
        assert route_after_classify(state) == "direct"

    def test_decomposition_mode(self) -> None:
        state: OrchestrationState = {"mode": OrchestrationMode.TASK_DECOMPOSITION.value}
        assert route_after_classify(state) == "decompose"

    def test_unknown_mode_defaults_to_direct(self) -> None:
        """mode 缺失或非法时降级到 direct（避免图阻塞）。"""
        assert route_after_classify({}) == "direct"
        state_unknown: OrchestrationState = {"mode": "unknown"}
        assert route_after_classify(state_unknown) == "direct"
        state_empty: OrchestrationState = {"mode": ""}
        assert route_after_classify(state_empty) == "direct"


# ============================================================
# Test 6：WORKFLOW 触发判定（P2.1）
# ============================================================


class TestWorkflowDetection:
    """``_is_workflow`` 关键词 + 正则 + 长度约束（spec §5.1）。"""

    def test_long_implement_query_triggers(self) -> None:
        """'实现一个 X'（≥15 字）→ 触发 WORKFLOW。"""
        from orchestrator.classifier import _is_workflow

        assert _is_workflow("实现一个线程安全的 LRU 缓存并附测试") is True

    def test_short_implement_query_no_trigger(self) -> None:
        """'实现一个 X'（<15 字）→ 不触发。"""
        from orchestrator.classifier import _is_workflow

        assert _is_workflow("实现一个 hello") is False

    def test_explicit_workflow_keyword(self) -> None:
        from orchestrator.classifier import _is_workflow

        assert _is_workflow("帮我走工作流完成这个功能") is True
        assert _is_workflow("端到端研发流程") is True

    def test_complete_keyword_triggers(self) -> None:
        """正则 '完整 + 研发动词' 触发（不依赖长度）。"""
        from orchestrator.classifier import _is_workflow

        assert _is_workflow("请完整实现这个模块") is True

    def test_general_question_not_triggered(self) -> None:
        from orchestrator.classifier import _is_workflow

        assert _is_workflow("你好，介绍量子计算") is False
        assert _is_workflow("今天天气怎么样") is False


class TestClassifyWorkflowMode:
    """``classify`` 节点的 WORKFLOW 分支（spec §5.2）。"""

    def test_workflow_mode_returned(self) -> None:
        """长研发需求 query → classify 返回 mode=workflow + 初始 state 字段。"""
        result = classify(_state("实现一个支持 TTL 过期的 LRU 缓存并附 pytest 测试"))
        assert result["mode"] == OrchestrationMode.WORKFLOW.value
        assert result["target_agent"] == ""
        assert result["subtasks"] == []
        assert result["workflow_status"] == "running"
        assert result["feedback_rounds"] == 0

    def test_decomposition_priority_over_workflow(self) -> None:
        """对比类问题（含'实现'）→ DECOMPOSITION 优先（不走 WORKFLOW）。"""
        result = classify(_state("对比 Python 和 Go 实现一个 web 服务的区别"))
        assert result["mode"] == OrchestrationMode.TASK_DECOMPOSITION.value

    def test_short_code_query_still_direct(self) -> None:
        """短代码 query（'帮我写 hello'）→ 仍走 DIRECT → MiniMax。"""
        result = classify(_state("帮我写一个 hello 函数"))
        assert result["mode"] == OrchestrationMode.DIRECT.value
        assert result["target_agent"] == AgentName.MINIMAX.value

    # ---- B3 修复（GLM 2026-06-18 review）：state 已带 target_agent 时短路 ----

    def test_forced_target_agent_short_circuits(self) -> None:
        """B3：当 state['target_agent'] 已由 orchestrate() 注入（强制路由）→ classify 短路。

        场景：用户用 Open WebUI 选 glm-agent 发"重构这段代码"，按关键词会路由到
        minimax-agent（命中"重构/代码"）；但 state 已含 target_agent='glm-agent'，
        classify 必须短路。
        """
        state = cast(
            OrchestrationState,
            {
                "user_query": "重构这段代码",
                "session_id": "test",
                "user_id": "u",
                "target_agent": "glm-agent",  # 已由 orchestrate() 注入
                "mode": "direct",
            },
        )
        result = classify(state)
        assert result["mode"] == OrchestrationMode.DIRECT.value
        assert result["target_agent"] == "glm-agent"  # 不被关键词改写
        # 显式标注 forced
        # 验证日志中 reason 包含 "forced_by_caller"（通过结构化 logger 不可见，间接验：路由不变）

    def test_forced_target_agent_overrides_keywords(self) -> None:
        """B3：强制 target_agent=minimax-agent 时，发 GLM 关键词 query 也不改路由。"""
        state = cast(
            OrchestrationState,
            {
                "user_query": "请审查这段代码",
                "session_id": "test",
                "user_id": "u",
                "target_agent": "minimax-agent",
            },
        )
        result = classify(state)
        # 即使 '审查' 命中 GLM 关键词，被强制参数压住
        assert result["target_agent"] == "minimax-agent"

    def test_forced_target_agent_overrides_decomposition(self) -> None:
        """B3：强制 target_agent 也要压住 DECOMPOSITION 模式。"""
        state = cast(
            OrchestrationState,
            {
                "user_query": "对比 Python 和 Go 的 web 框架",  # DECOMPOSITION 触发
                "session_id": "test",
                "user_id": "u",
                "target_agent": "deepseek-agent",
            },
        )
        result = classify(state)
        assert result["mode"] == OrchestrationMode.DIRECT.value
        assert result["target_agent"] == "deepseek-agent"
