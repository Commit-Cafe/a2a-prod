"""sdlc_prompts 模板契约测试（P2.1）。"""

from __future__ import annotations

import orchestrator.sdlc_prompts as prompts


def test_all_prompts_have_user_query_placeholder() -> None:
    """DeepSeek/GLM-spec 模板必须含 {user_query} 占位（.format 时要填充）。"""
    assert "{user_query}" in prompts.DEEPSEEK_SPEC_PROMPT
    assert "{user_query}" in prompts.GLM_TECH_DESIGN_PROMPT


def test_glm_tech_design_prompt_has_spec_placeholder() -> None:
    """GLM 技术规范模板必须含 {spec} 占位。"""
    assert "{spec}" in prompts.GLM_TECH_DESIGN_PROMPT


def test_minimax_code_prompt_has_tech_design_and_session_placeholders() -> None:
    """MiniMax 编码模板必须含 {tech_design} / {session_id} / {feedback_block} 占位。"""
    assert "{tech_design}" in prompts.MINIMAX_CODE_PROMPT
    assert "{session_id}" in prompts.MINIMAX_CODE_PROMPT
    assert "{feedback_block}" in prompts.MINIMAX_CODE_PROMPT


def test_glm_feedback_prompt_has_blocker_placeholder() -> None:
    """GLM 反馈模板必须含 {blocker} 占位。"""
    assert "{blocker}" in prompts.GLM_FEEDBACK_PROMPT


def test_prompts_contain_hard_constraints() -> None:
    """四段模板都含角色硬约束关键词。"""
    # DeepSeek: 不写代码
    assert "不写实现代码" in prompts.DEEPSEEK_SPEC_PROMPT
    # GLM tech design: 严禁输出实现代码
    assert "严禁" in prompts.GLM_TECH_DESIGN_PROMPT
    assert "代码" in prompts.GLM_TECH_DESIGN_PROMPT
    # MiniMax: 遇阻 [NEED_HELP] + 严格遵循
    assert "[NEED_HELP]" in prompts.MINIMAX_CODE_PROMPT
    assert "严格遵循" in prompts.MINIMAX_CODE_PROMPT
    # GLM feedback: 不替 MiniMax 写完整代码
    assert "不要替 MiniMax 写完整代码" in prompts.GLM_FEEDBACK_PROMPT


def test_all_prompts_format_without_error() -> None:
    """四段模板用正确 kwargs 调 .format 不抛 KeyError。"""
    prompts.DEEPSEEK_SPEC_PROMPT.format(user_query="需求 X")
    prompts.GLM_TECH_DESIGN_PROMPT.format(spec="# Spec", user_query="需求 X")
    prompts.MINIMAX_CODE_PROMPT.format(tech_design="# Tech", session_id="s1", feedback_block="")
    prompts.GLM_FEEDBACK_PROMPT.format(blocker="阻塞", tech_design="# Tech", prior_feedback="无")
