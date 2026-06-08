"""P5: OpenAI 兼容层契约测试（不需要真实起服务，仅测模块函数）。

覆盖：

- :class:`ChatCompletionResponse` 字段顺序 / 类型 / 命名（与 OpenAI 官方对齐）
- :func:`build_chat_completion_response` 转换逻辑
- :func:`list_models` 返回 3 个 Agent + `auto`
- :func:`extract_user_query` 提取规则
- :func:`resolve_target_agent` 路由决策
- Pydantic schema 的 `model_config = extra="allow"` 兼容 Open WebUI 多余字段

参考：
- OpenAI Chat Completions API 2024-01 公开契约
- SPEC §3.9
- ADR-0009
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from orchestrator.openai_compat import (
    SUPPORTED_MODELS,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelListResponse,
    build_chat_completion_response,
    extract_user_query,
    is_supported_model,
    list_models,
    resolve_target_agent,
)
from orchestrator.openai_compat import ChatMessage

# ============================================================
# Schema 字段对齐
# ============================================================


class TestChatCompletionResponseSchema:
    """验证响应字段名 / 类型 / 值与 OpenAI 官方 100% 一致。"""

    def test_response_has_all_required_openai_fields(self) -> None:
        resp = ChatCompletionResponse(
            id="chatcmpl-xxx",
            created=1717800000,
            model="auto",
            choices=[],
        )
        dumped = resp.model_dump()
        # OpenAI 官方必含字段
        assert dumped["object"] == "chat.completion"
        assert isinstance(dumped["id"], str)
        assert dumped["id"].startswith("chatcmpl-")
        assert isinstance(dumped["created"], int)
        assert isinstance(dumped["model"], str)
        assert isinstance(dumped["choices"], list)
        assert "usage" in dumped
        assert set(dumped["usage"].keys()) == {
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
        }

    def test_response_serializes_to_json(self) -> None:
        """Open WebUI 用 JSON 解析响应，必须可序列化。"""
        resp = ChatCompletionResponse(
            id="chatcmpl-test",
            created=1717800000,
            model="glm-agent",
            choices=[],
        )
        s = resp.model_dump_json()
        parsed = json.loads(s)
        assert parsed["model"] == "glm-agent"
        assert parsed["object"] == "chat.completion"

    def test_response_default_usage_is_zero(self) -> None:
        """本阶段 usage 字段为占位（SPEC §3.9.5）。"""
        resp = ChatCompletionResponse(
            id="x", created=0, model="auto", choices=[]
        )
        assert resp.usage.prompt_tokens == 0
        assert resp.usage.completion_tokens == 0
        assert resp.usage.total_tokens == 0


class TestModelListResponseSchema:
    """验证 /v1/models 响应结构。"""

    def test_returns_three_agents(self) -> None:
        ml = list_models()
        assert ml.object == "list"
        ids = {m.id for m in ml.data}
        assert ids == set(SUPPORTED_MODELS)
        assert ids == {"glm-agent", "deepseek-agent", "minimax-agent"}

    def test_model_objects_have_required_fields(self) -> None:
        ml = list_models()
        for m in ml.data:
            assert m.id
            assert m.object == "model"
            assert isinstance(m.created, int)
            assert m.owned_by == "a2a-prod"


class TestChatCompletionRequestSchema:
    """验证请求 schema 的容错性。"""

    def test_default_model_is_auto(self) -> None:
        req = ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")])
        assert req.model == "auto"
        assert req.stream is False

    def test_extra_fields_allowed(self) -> None:
        """Open WebUI 可能多塞 ``user`` / ``metadata`` 等字段，不能 422。"""
        req = ChatCompletionRequest.model_validate(
            {
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
                "user": "test-user-uuid",  # Open WebUI 会塞
                "metadata": {"chat_id": "abc"},  # Open WebUI 会塞
                "top_p": 0.9,  # Open WebUI 会塞
            }
        )
        assert req.model == "auto"

    def test_messages_must_have_at_least_one(self) -> None:
        with pytest.raises(ValueError):
            ChatCompletionRequest(messages=[])

    def test_role_must_be_known(self) -> None:
        """tool / function 角色本阶段不支持（SPEC §3.9.5）。"""
        with pytest.raises(ValueError):
            ChatCompletionRequest(
                messages=[{"role": "tool", "content": "x"}]  # type: ignore[list-item]
            )


# ============================================================
# 转换函数逻辑
# ============================================================


class TestBuildChatCompletionResponse:
    def test_basic_state_to_response(self) -> None:
        state: dict[str, Any] = {
            "final_answer": "对比结论：Python 适合快速原型，Go 适合高并发服务。",
            "mode": "direct",
            "target_agent": "deepseek-agent",
            "agent_responses": {"deepseek-agent": "对比结论：..."},
        }
        resp = build_chat_completion_response(state=state, model="deepseek-agent")

        assert resp.model == "deepseek-agent"
        assert resp.choices[0].message.role == "assistant"
        assert resp.choices[0].message.content.startswith("对比结论：")
        assert resp.choices[0].finish_reason == "stop"
        assert resp.id.startswith("chatcmpl-")
        assert resp.object == "chat.completion"

    def test_empty_final_answer(self) -> None:
        state: dict[str, Any] = {"final_answer": "", "mode": "direct"}
        resp = build_chat_completion_response(state=state, model="auto")
        assert resp.choices[0].message.content == ""

    def test_missing_final_answer_key(self) -> None:
        """防御：state 里没有 final_answer 时不要 KeyError。"""
        state: dict[str, Any] = {"mode": "direct"}
        resp = build_chat_completion_response(state=state, model="auto")
        assert resp.choices[0].message.content == ""


class TestExtractUserQuery:
    def test_simple_user_message(self) -> None:
        msgs = [ChatMessage(role="user", content="你好")]
        assert extract_user_query(msgs) == "你好"

    def test_user_with_system_prefix(self) -> None:
        """system 消息应作为前缀保留。"""
        msgs = [
            ChatMessage(role="system", content="你是助手"),
            ChatMessage(role="user", content="你好"),
        ]
        out = extract_user_query(msgs)
        assert "你是助手" in out
        assert "你好" in out

    def test_multiple_system_messages_concatenated(self) -> None:
        msgs = [
            ChatMessage(role="system", content="规则 1"),
            ChatMessage(role="system", content="规则 2"),
            ChatMessage(role="user", content="问题"),
        ]
        out = extract_user_query(msgs)
        assert "规则 1" in out
        assert "规则 2" in out
        assert out.endswith("问题")

    def test_takes_last_user_message(self) -> None:
        """Open WebUI 多轮对话时取最后一条 user。"""
        msgs = [
            ChatMessage(role="user", content="第一个问题"),
            ChatMessage(role="assistant", content="回答 1"),
            ChatMessage(role="user", content="追问"),
        ]
        out = extract_user_query(msgs)
        assert "第一个问题" not in out
        assert "追问" in out

    def test_no_user_message_returns_empty(self) -> None:
        msgs = [ChatMessage(role="assistant", content="hi")]
        assert extract_user_query(msgs) == ""

    def test_only_system_returns_empty(self) -> None:
        msgs = [ChatMessage(role="system", content="你是助手")]
        assert extract_user_query(msgs) == ""


class TestResolveTargetAgent:
    @pytest.mark.parametrize(
        ("requested", "expected"),
        [
            ("glm-agent", "glm-agent"),
            ("deepseek-agent", "deepseek-agent"),
            ("minimax-agent", "minimax-agent"),
            ("auto", ""),
            ("gpt-4", ""),  # 未知值兜底
            ("", ""),  # 空值
        ],
    )
    def test_routing_decision(self, requested: str, expected: str) -> None:
        assert resolve_target_agent(requested) == expected


class TestIsSupportedModel:
    def test_known_models(self) -> None:
        for m in SUPPORTED_MODELS:
            assert is_supported_model(m)

    def test_auto_is_supported(self) -> None:
        assert is_supported_model("auto")

    def test_unknown_model_returns_false(self) -> None:
        assert is_supported_model("gpt-4") is False
        assert is_supported_model("") is False
