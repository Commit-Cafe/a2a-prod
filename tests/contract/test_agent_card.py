"""Agent Card 契约测试（SPEC §1.1 守护点）。

校验三个 Agent（GLM / DeepSeek / MiniMax）的 ``build_card()`` 生成的 ``AgentCard``
满足 SPEC §1.1 MUST 字段。同时验证 SPEC §2.2 差异约束。

P1-3 阶段：扩展为三 Agent 参数化 + 各自差异点测试。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from agents.base_agent import BaseAgent
from agents.deepseek_agent.agent import DeepSeekAgent
from agents.glm_agent.agent import GLMAgent
from agents.minimax_agent.agent import MiniMaxAgent

# ============================================================
# 三 Agent 工厂（用于参数化）
# ============================================================


AgentFactory = Callable[[], BaseAgent]


AGENTS: tuple[tuple[str, AgentFactory, str, str], ...] = (
    ("glm", GLMAgent, "glm-agent", "glm-5"),
    ("deepseek", DeepSeekAgent, "deepseek-agent", "deepseek-chat"),
    ("minimax", MiniMaxAgent, "minimax-agent", "MiniMax-M3"),
)


def _build_card_dict(factory: AgentFactory) -> dict[str, object]:
    """实例化 Agent 并 dump 成 dict（与运行时 /.well-known/agent.json 输出一致）。"""
    agent = factory()
    return agent.build_card(public_url="http://test:8000/").model_dump(exclude_none=True)


# ============================================================
# SPEC §1.1 Agent Card 强制字段（参数化：三个 Agent 都跑）
# ============================================================


@pytest.mark.parametrize(
    "factory",
    [pytest.param(f, id=name) for name, f, _, _ in AGENTS],
)
class TestAgentCardRequiredFields:
    """SPEC §1.1 MUST 字段（三 Agent 共享）。"""

    def test_has_name(self, factory: AgentFactory) -> None:
        card = _build_card_dict(factory)
        assert isinstance(card.get("name"), str) and card["name"]

    def test_has_description(self, factory: AgentFactory) -> None:
        card = _build_card_dict(factory)
        assert isinstance(card.get("description"), str) and card["description"]

    def test_has_version(self, factory: AgentFactory) -> None:
        card = _build_card_dict(factory)
        v = card.get("version")
        assert isinstance(v, str)
        parts = v.split(".")
        assert len(parts) == 3, f"version must be SemVer, got {v!r}"
        assert all(p.isdigit() for p in parts)

    def test_has_url(self, factory: AgentFactory) -> None:
        card = _build_card_dict(factory)
        assert isinstance(card.get("url"), str)
        assert str(card["url"]).startswith(("http://", "https://"))

    def test_capabilities_streaming_true(self, factory: AgentFactory) -> None:
        card = _build_card_dict(factory)
        caps = card.get("capabilities")
        assert isinstance(caps, dict)
        assert caps.get("streaming") is True

    def test_capabilities_push_notifications_false(self, factory: AgentFactory) -> None:
        """SPEC §1.1：P0+P1 MUST 设为 false。"""
        card = _build_card_dict(factory)
        caps = card.get("capabilities")
        assert isinstance(caps, dict)
        assert caps.get("pushNotifications") is False

    def test_has_at_least_one_skill(self, factory: AgentFactory) -> None:
        card = _build_card_dict(factory)
        skills = card.get("skills")
        assert isinstance(skills, list)
        assert len(skills) >= 1


# ============================================================
# SPEC §1.1 skill 字段（参数化）
# ============================================================


@pytest.mark.parametrize(
    "factory",
    [pytest.param(f, id=name) for name, f, _, _ in AGENTS],
)
class TestAgentCardSkillFields:
    """SPEC §1.1 skill 字段。"""

    def test_skill_has_id_name_description(self, factory: AgentFactory) -> None:
        card = _build_card_dict(factory)
        skills = card["skills"]
        assert isinstance(skills, list)
        for skill in skills:
            assert isinstance(skill, dict)
            assert skill.get("id"), "skill.id MUST 非空"
            assert skill.get("name"), "skill.name MUST 非空"
            assert skill.get("description"), "skill.description MUST 非空"


# ============================================================
# SPEC §1.1 SHOULD 字段（参数化）
# ============================================================


@pytest.mark.parametrize(
    "factory",
    [pytest.param(f, id=name) for name, f, _, _ in AGENTS],
)
class TestAgentCardProtocol:
    """SPEC §1.1 SHOULD 字段。"""

    def test_protocol_version_is_str(self, factory: AgentFactory) -> None:
        """与 a2a-sdk 0.3.x 对齐（ADR-0005）。"""
        card = _build_card_dict(factory)
        pv = card.get("protocolVersion")
        if pv is not None:
            assert isinstance(pv, str)

    def test_default_input_modes_text(self, factory: AgentFactory) -> None:
        card = _build_card_dict(factory)
        modes = card.get("defaultInputModes")
        if modes is not None:
            assert isinstance(modes, list)
            assert "text" in modes

    def test_default_output_modes_text(self, factory: AgentFactory) -> None:
        card = _build_card_dict(factory)
        modes = card.get("defaultOutputModes")
        if modes is not None:
            assert isinstance(modes, list)
            assert "text" in modes


# ============================================================
# SPEC §2.2 差异约束（三 Agent 各自）
# ============================================================


@pytest.mark.parametrize(
    "factory,expected_name,expected_model",
    [pytest.param(f, n, m, id=name) for name, f, n, m in AGENTS],
)
class TestAgentSpecifics:
    """SPEC §2.2 差异约束（参数化覆盖三 Agent）。"""

    def test_name_matches(
        self, factory: AgentFactory, expected_name: str, expected_model: str
    ) -> None:
        agent = factory()
        assert agent.name == expected_name

    def test_model_name_matches(
        self, factory: AgentFactory, expected_name: str, expected_model: str
    ) -> None:
        agent = factory()
        assert agent.model_name == expected_model

    def test_litellm_model_string_uses_openai_prefix(
        self,
        factory: AgentFactory,
        expected_name: str,
        expected_model: str,
    ) -> None:
        """SPEC §2.3：MUST 走 openai/<model> 路由。"""
        agent = factory()
        assert agent._build_litellm_model() == f"openai/{expected_model}"

    def test_skills_non_empty(
        self, factory: AgentFactory, expected_name: str, expected_model: str
    ) -> None:
        agent = factory()
        assert len(agent.skills) >= 1

    def test_default_instruction_non_empty(
        self,
        factory: AgentFactory,
        expected_name: str,
        expected_model: str,
    ) -> None:
        agent = factory()
        instruction = agent.default_instruction()
        assert isinstance(instruction, str) and instruction.strip()


# ============================================================
# SPEC §2.2 GLM Agent 差异约束（保留 P0 阶段的硬编码断言）
# ============================================================


class TestGLMAgentSpecifics:
    """SPEC §2.2 GLM Agent 差异约束（保留 P0 阶段硬编码，便于回归）。"""

    def test_name_is_glm_agent(self) -> None:
        assert GLMAgent().name == "glm-agent"

    def test_model_name_is_glm_5(self) -> None:
        assert GLMAgent().model_name == "glm-5"

    def test_litellm_model_string_uses_openai_prefix(self) -> None:
        """SPEC §2.3：MUST 走 openai/<model> 路由。"""
        agent = GLMAgent()
        assert agent._build_litellm_model() == "openai/glm-5"


# ============================================================
# SPEC §2.2 DeepSeek Agent 差异约束
# ============================================================


class TestDeepSeekAgentSpecifics:
    """SPEC §2.2 DeepSeek Agent 差异约束。"""

    def test_name_is_deepseek_agent(self) -> None:
        assert DeepSeekAgent().name == "deepseek-agent"

    def test_model_name_is_deepseek_chat(self) -> None:
        assert DeepSeekAgent().model_name == "deepseek-chat"

    def test_skill_is_requirement_analysis(self) -> None:
        """SPEC §2.2：DeepSeek = 产品经理 + 技术总监。"""
        skills = DeepSeekAgent().skills
        assert len(skills) == 1
        assert skills[0].id == "requirement-analysis"

    def test_litellm_model_string_uses_openai_prefix(self) -> None:
        agent = DeepSeekAgent()
        assert agent._build_litellm_model() == "openai/deepseek-chat"


# ============================================================
# SPEC §2.2 MiniMax Agent 差异约束
# ============================================================


class TestMiniMaxAgentSpecifics:
    """SPEC §2.2 MiniMax Agent 差异约束。"""

    def test_name_is_minimax_agent(self) -> None:
        assert MiniMaxAgent().name == "minimax-agent"

    def test_model_name_is_m3(self) -> None:
        assert MiniMaxAgent().model_name == "MiniMax-M3"

    def test_skill_is_code_writing(self) -> None:
        """SPEC §2.2：MiniMax = 代码工程型。"""
        skills = MiniMaxAgent().skills
        assert len(skills) == 1
        assert skills[0].id == "code-writing"

    def test_litellm_model_string_uses_openai_prefix(self) -> None:
        agent = MiniMaxAgent()
        assert agent._build_litellm_model() == "openai/MiniMax-M3"
