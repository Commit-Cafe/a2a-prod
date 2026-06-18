"""P5: OpenAI 兼容层契约测试（不需要真实起服务，仅测模块函数）。

覆盖：

- :class:`ChatCompletionResponse` 字段顺序 / 类型 / 命名（与 OpenAI 官方对齐）
- :func:`build_chat_completion_response` 转换逻辑
- :func:`list_models` 返回 3 个 Agent + `auto`
- :func:`extract_user_query` 提取规则
- :func:`resolve_target_agent` 路由决策
- Pydantic schema 的 `model_config = extra="allow"` 兼容 Open WebUI 多余字段
- S6 修复（GLM 2026-06-18 review）：HTTP 级测试——强制 Agent / 流式 / 鉴权
- S4 修复：verify_api_key 用 hmac.compare_digest（常数时间比较）

参考：
- OpenAI Chat Completions API 2024-01 公开契约
- SPEC §3.9
- ADR-0009
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from orchestrator.openai_compat import (
    SUPPORTED_MODELS,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    build_chat_completion_response,
    extract_user_query,
    is_supported_model,
    list_models,
    resolve_target_agent,
)

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
        resp = ChatCompletionResponse(id="x", created=0, model="auto", choices=[])
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
        """Open WebUI 多轮对话时取最后一条 user 作主问题；前序 user 进对话历史。

        S8 修复后：第一条 user 进【对话历史】块，最后一条 user 作为【当前问题】。
        测试验证"最后一条 user 是 query 主问题"。
        """
        msgs = [
            ChatMessage(role="user", content="第一个问题"),
            ChatMessage(role="assistant", content="回答 1"),
            ChatMessage(role="user", content="追问"),
        ]
        out = extract_user_query(msgs)
        # 追问是当前问题（不是历史）
        assert "追问" in out
        assert out.rstrip().endswith("追问")
        # 第一条 user 在历史里（S8 修复后保留）
        assert "第一个问题" in out
        assert "回答 1" in out

    def test_no_user_message_returns_empty(self) -> None:
        msgs = [ChatMessage(role="assistant", content="hi")]
        assert extract_user_query(msgs) == ""

    def test_only_system_returns_empty(self) -> None:
        msgs = [ChatMessage(role="system", content="你是助手")]
        assert extract_user_query(msgs) == ""

    # ---- S8 修复（GLM 2026-06-18 review）：多轮对话保留 assistant 历史 ----

    def test_multiturn_keeps_assistant_history(self) -> None:
        """S8：多轮对话中 assistant 历史应被拼入（避免失忆）。"""
        msgs = [
            ChatMessage(role="user", content="第一个问题"),
            ChatMessage(role="assistant", content="回答 1"),
            ChatMessage(role="user", content="追问"),
        ]
        out = extract_user_query(msgs)
        # 当前问题：追问
        assert "当前问题" in out
        assert "追问" in out
        assert out.rstrip().endswith("追问")
        # 历史：第一个问题 + 回答 1
        assert "对话历史" in out
        assert "第一个问题" in out
        assert "回答 1" in out
        assert "user" in out  # 历史中带 role 标签
        assert "assistant" in out

    def test_single_turn_unchanged(self) -> None:
        """S8：单轮对话（无历史）行为应保持向后兼容。"""
        msgs = [ChatMessage(role="user", content="hi")]
        out = extract_user_query(msgs)
        assert out == "hi"  # 严格等于（无前缀/后缀）
        # 单轮：不应有"对话历史"/"当前问题"块
        assert "对话历史" not in out
        assert "当前问题" not in out

    def test_single_turn_with_system_unchanged(self) -> None:
        """S8：单轮 + system 行为向后兼容（"你是助手\n\nhi" 形态）。"""
        msgs = [
            ChatMessage(role="system", content="你是助手"),
            ChatMessage(role="user", content="hi"),
        ]
        out = extract_user_query(msgs)
        assert "你是助手" in out
        assert "hi" in out
        assert out.endswith("hi")
        assert "对话历史" not in out
        assert "当前问题" not in out

    def test_multiturn_with_system_prefix(self) -> None:
        """S8：多轮 + system prompt 三件套全保留。"""
        msgs = [
            ChatMessage(role="system", content="你是助手"),
            ChatMessage(role="user", content="hi"),
            ChatMessage(role="assistant", content="hello"),
            ChatMessage(role="user", content="bye"),
        ]
        out = extract_user_query(msgs)
        assert "你是助手" in out
        assert "对话历史" in out
        assert "hi" in out
        assert "hello" in out
        assert "当前问题" in out
        assert "bye" in out


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


# ============================================================
# S6 修复（GLM 2026-06-18 review）：HTTP 级测试（fastapi TestClient）
# ============================================================


def _make_test_client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, list[dict[str, Any]]]:
    """构造 TestClient + 捕获 orchestrate() 调用参数的 helper。

    通过 monkeypatch 替换 ``orchestrator.__main__.orchestrate``，
    避免真实起 Agent / LLM。返回 (client, captured_calls)。
    """
    captured: list[dict[str, Any]] = []

    async def fake_orchestrate(
        user_query: str,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        target_agent: str | None = None,
    ) -> dict[str, Any]:
        captured.append(
            {
                "user_query": user_query,
                "session_id": session_id,
                "user_id": user_id,
                "target_agent": target_agent,
            }
        )
        return {
            "final_answer": "fake response",
            "mode": "direct",
            "target_agent": target_agent or "glm-agent",
            "agent_responses": {target_agent or "glm-agent": "fake response"},
            "errors": [],
            "session_id": session_id or "test-session",
        }

    # 必须在 import __main__ 之前先 patch（__main__ 内部 import orchestrate）
    # 实际上 __main__ 在 import 时已绑定了 orchestrate 函数引用，
    # 所以这里 patch __main__ 模块的 orchestrate 属性即可
    from orchestrator import __main__ as orch_main  # noqa: PLC0415

    monkeypatch.setattr(orch_main, "orchestrate", fake_orchestrate)

    # 跳过 lifespan 里的 build_graph（避免真实编译图）
    from orchestrator import graph as orch_graph  # noqa: PLC0415

    monkeypatch.setattr(orch_graph, "build_graph", lambda: None)

    # 跳过 __main__ lifespan 里的 setup_agent（不需要真实 Langfuse）
    async def _noop_setup_agent() -> None:  # noqa: D401
        return None

    monkeypatch.setattr("observability.setup.setup_agent", _noop_setup_agent, raising=False)

    app = orch_main.create_app()
    return TestClient(app), captured


def test_list_models_endpoint() -> None:
    """GET /v1/models 返回 3 个 Agent。"""
    from orchestrator import __main__ as orch_main  # noqa: PLC0415

    async def _noop() -> None:  # noqa: D401
        return None

    import pytest as _pytest  # noqa: PLC0415

    _pytest.MonkeyPatch().setattr("observability.setup.setup_agent", _noop, raising=False)
    from orchestrator import graph as _g  # noqa: PLC0415

    _pytest.MonkeyPatch().setattr(_g, "build_graph", lambda: None)

    app = orch_main.create_app()
    client = TestClient(app)
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert {m["id"] for m in data["data"]} == {"glm-agent", "deepseek-agent", "minimax-agent"}


def test_health_endpoint() -> None:
    """GET /health 不依赖 orchestrate。"""
    import pytest as _pytest  # noqa: PLC0415

    from orchestrator import __main__ as orch_main  # noqa: PLC0415
    from orchestrator import graph as _g  # noqa: PLC0415

    _pytest.MonkeyPatch().setattr(_g, "build_graph", lambda: None)
    async def _noop() -> None:
        return None

    _pytest.MonkeyPatch().setattr("observability.setup.setup_agent", _noop, raising=False)
    app = orch_main.create_app()
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


class TestChatCompletionsHTTP:
    """S6：/v1/chat/completions HTTP 级测试。"""

    def test_forces_agent_when_model_equals_agent_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B3 修复：model=glm-agent → target_agent='glm-agent' 传给 orchestrate。"""
        client, captured = _make_test_client(monkeypatch)
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "minimax-agent",
                "messages": [{"role": "user", "content": "重构这段代码"}],
            },
        )
        assert resp.status_code == 200
        assert len(captured) == 1
        assert captured[0]["target_agent"] == "minimax-agent"
        # response.model 也回填
        body = resp.json()
        assert body["model"] == "minimax-agent"

    def test_forces_each_supported_agent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B3：三个具体 Agent 都要走强制路由。"""
        for agent in ("glm-agent", "deepseek-agent", "minimax-agent"):
            client, captured = _make_test_client(monkeypatch)
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": agent,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            assert resp.status_code == 200, f"failed for {agent}"
            assert captured[0]["target_agent"] == agent

    def test_auto_model_uses_classify(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """model=auto → target_agent=None 让 classify 路由。"""
        client, captured = _make_test_client(monkeypatch)
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "你好"}],
            },
        )
        assert resp.status_code == 200
        # target_agent 传 None（不要传空串，让 classify 路由）
        assert captured[0]["target_agent"] is None

    def test_unknown_model_falls_back_to_auto(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """model='gpt-4' 等未知值 → effective_model='auto'，target_agent=None。"""
        client, captured = _make_test_client(monkeypatch)
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4-turbo",  # Open WebUI 可能传
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert resp.status_code == 200
        assert captured[0]["target_agent"] is None

    def test_stream_returns_sse_chunks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S6：stream=True → SSE 格式（chat.completion.chunk + data: [DONE]）。"""
        client, captured = _make_test_client(monkeypatch)
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "minimax-agent",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            chunks: list[str] = []
            for line in resp.iter_lines():
                chunks.append(line)

        # 必须包含 [DONE] 收尾
        joined = "\n".join(chunks)
        assert "data: [DONE]" in joined
        # 必须包含至少一个 chat.completion.chunk
        assert "chat.completion.chunk" in joined
        # S3 修复：必须包含 keep-alive 心跳
        assert ": keepalive" in joined
        # B3 修复：强制 Agent 也传到 stream
        assert len(captured) == 1
        assert captured[0]["target_agent"] == "minimax-agent"

    def test_no_user_message_returns_400(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """messages 全是 assistant（无 user）→ 400。"""
        client, _ = _make_test_client(monkeypatch)
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "assistant", "content": "hi"}],
            },
        )
        assert resp.status_code == 400
        assert "user" in resp.json()["detail"].lower()


class TestVerifyApiKey:
    """S4 修复：verify_api_key 用 hmac.compare_digest（常数时间）。"""

    def test_no_key_configured_allows_all(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未配置 ORCHESTRATOR_API_KEY / LITELLM_MASTER_KEY → 跳过鉴权。"""
        monkeypatch.delenv("ORCHESTRATOR_API_KEY", raising=False)
        monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
        from orchestrator import __main__ as orch_main  # noqa: PLC0415

        assert orch_main._expected_api_key() is None

    def test_correct_key_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bearer 头值匹配 → 通过。"""
        from fastapi import Request  # noqa: PLC0415

        from orchestrator import __main__ as orch_main  # noqa: PLC0415

        monkeypatch.setenv("ORCHESTRATOR_API_KEY", "test-key-abc")

        # 构造一个伪 Request 对象（足够触发 verify_api_key）
        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer test-key-abc")],
        }
        req = Request(scope)

        # 不应抛 HTTPException
        orch_main.verify_api_key(req)

    def test_wrong_key_raises_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bearer 头值不匹配 → 401。"""
        from fastapi import HTTPException, Request  # noqa: PLC0415

        from orchestrator import __main__ as orch_main  # noqa: PLC0415

        monkeypatch.setenv("ORCHESTRATOR_API_KEY", "test-key-abc")
        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer wrong-key")],
        }
        req = Request(scope)
        with pytest.raises(HTTPException) as exc_info:
            orch_main.verify_api_key(req)
        assert exc_info.value.status_code == 401

    def test_missing_auth_header_raises_401(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无 Authorization 头 → 401。"""
        from fastapi import HTTPException, Request  # noqa: PLC0415

        from orchestrator import __main__ as orch_main  # noqa: PLC0415

        monkeypatch.setenv("ORCHESTRATOR_API_KEY", "test-key-abc")
        scope = {"type": "http", "headers": []}
        req = Request(scope)
        with pytest.raises(HTTPException) as exc_info:
            orch_main.verify_api_key(req)
        assert exc_info.value.status_code == 401

    def test_uses_hmac_constant_time_compare(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S4：verify_api_key 实现里包含 hmac.compare_digest 调用（防御时序侧信道）。

        通过 inspection 而非行为测试——直接断言模块内 verify_api_key 源码
        包含 ``hmac.compare_digest`` 字串。
        """
        import inspect  # noqa: PLC0415

        from orchestrator import __main__ as orch_main  # noqa: PLC0415

        source = inspect.getsource(orch_main.verify_api_key)
        assert "hmac.compare_digest" in source
        # 防御性：旧实现 'provided != expected' 不能还在
        assert "provided != expected" not in source

    def test_http_endpoint_401_on_wrong_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S6：HTTP 端点：错 key → 401。"""
        monkeypatch.setenv("ORCHESTRATOR_API_KEY", "correct-key")
        from orchestrator import __main__ as orch_main  # noqa: PLC0415
        from orchestrator import graph as _g  # noqa: PLC0415

        async def _noop() -> None:
            return None

        monkeypatch.setattr(_g, "build_graph", lambda: None)
        monkeypatch.setattr("observability.setup.setup_agent", _noop, raising=False)
        app = orch_main.create_app()
        client = TestClient(app)

        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401

    def test_http_endpoint_ok_on_correct_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S6：HTTP 端点：正 key → 200。"""
        client, _ = _make_test_client(monkeypatch)
        monkeypatch.setenv("ORCHESTRATOR_API_KEY", "correct-key")
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "minimax-agent",
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"Authorization": "Bearer correct-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["model"] == "minimax-agent"
