"""SDLC 子图回路契约测试（mock Agent，无 LLM/docker）。

验证 spec §2.1 子图结构 + 反馈回路三种路径：
- happy path（无遇阻 → 一次通过）
- 一次反馈解决（MiniMax 第 1 次遇阻 → GLM 反馈 → 第 2 次通过）
- 达上限终止（MiniMax 永远遇阻 → 卡在 MAX_FEEDBACK_ROUNDS）

Mock 模式：参照 ``tests/contract/test_graph.py``，
用 ``patch.object(a2a_client, "message_send", new_callable=AsyncMock)``
+ ``side_effect`` 按 url 分流返回 deepseek/glm/minimax 响应。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator import a2a_client, sdlc_workflow
from orchestrator.sdlc_workflow import (
    MAX_FEEDBACK_ROUNDS,
    NEED_HELP_MARKER,
    build_sdlc_graph,
)


@pytest.fixture(autouse=True)
def _reset_sdlc_singleton() -> Iterator[None]:
    """每个测试前重置子图单例，避免跨测试 state 污染。"""
    sdlc_workflow._COMPILED_SDLC_GRAPH = None
    yield
    sdlc_workflow._COMPILED_SDLC_GRAPH = None


@pytest.fixture(autouse=True)
def _tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """重定向 WORKSPACE_SDLC_DIR 到 tmp_path/sdlc，避免污染真实 workspace。"""
    from orchestrator import sdlc_workspace

    target = tmp_path / "sdlc"
    monkeypatch.setattr(sdlc_workspace, "WORKSPACE_SDLC_DIR", target)
    return target


def _state(query: str = "实现一个 LRU 缓存") -> dict[str, Any]:
    """构造 SDLC 子图入口 state。"""
    return {
        "user_query": query,
        "session_id": "test-sess",
        "feedback_rounds": 0,
        "workflow_status": "running",
    }


def _make_send(
    *,
    deepseek: str = "# Spec",
    glm_spec: str = "# 技术规范",
    minimax_seq: list[str] | None = None,
    glm_feedback_seq: list[str] | None = None,
) -> AsyncMock:
    """构造按 url + prompt 内容分流的 message_send mock。

    分流逻辑（spec §4.2 节点签名）：
    - url 含 deepseek → 节点 1（deepseek_doc）
    - url 含 minimax  → 节点 3（minimax_code），按序消费 minimax_seq
    - url 含 glm 且 prompt 含"阻塞"/"NEED_HELP"/"之前已给过的反馈" → 节点 5（glm_feedback）
    - url 含 glm 其他 → 节点 2（glm_spec）
    """
    state = {"minimax_idx": 0, "glm_fb_idx": 0}
    minimax_seq = minimax_seq or ["已实现，pytest 全过\n[FILES_WRITTEN] code/lru.py"]
    glm_feedback_seq = glm_feedback_seq or []

    async def _side_effect(url: str, text: str, **kwargs: Any) -> str:
        if "deepseek" in url:
            return deepseek
        if "minimax" in url:
            resp = minimax_seq[min(state["minimax_idx"], len(minimax_seq) - 1)]
            state["minimax_idx"] += 1
            return resp
        if "glm" in url:
            # 区分 spec 节点 vs feedback 节点（看 prompt 关键词）
            if "阻塞" in text or "NEED_HELP" in text or "之前已给过的反馈" in text:
                if glm_feedback_seq:
                    resp = glm_feedback_seq[min(state["glm_fb_idx"], len(glm_feedback_seq) - 1)]
                    state["glm_fb_idx"] += 1
                    return resp
                return glm_spec  # 兜底
            return glm_spec
        return glm_spec

    mock = AsyncMock(side_effect=_side_effect)
    return mock


# ============================================================
# 子图结构
# ============================================================


class TestSdlcGraphStructure:
    def test_build_returns_compiled_graph(self) -> None:
        graph = build_sdlc_graph()
        assert hasattr(graph, "ainvoke")

    def test_graph_contains_four_nodes(self) -> None:
        graph = build_sdlc_graph()
        node_ids = set(graph.nodes.keys())
        assert {"deepseek_doc", "glm_spec", "minimax_code", "glm_feedback"} <= node_ids


# ============================================================
# happy path（无遇阻 → 一次通过）
# ============================================================


class TestHappyPath:
    async def test_no_feedback_rounds(self) -> None:
        """MiniMax 一次通过 → feedback_rounds=0，sdlc_doc 三段齐全。"""
        mock_send = _make_send(
            deepseek="# Spec\n实现 LRU",
            glm_spec="# 技术规范\n文件: lru.py",
            minimax_seq=["已实现 lru.py，pytest 全过\n[FILES_WRITTEN] code/lru.py"],
        )
        with patch.object(a2a_client, "message_send", mock_send):
            graph = build_sdlc_graph()
            result = await graph.ainvoke(_state())

        assert result["feedback_rounds"] == 0
        assert result.get("sdlc_feedback", []) == []
        doc = result["sdlc_doc"]
        assert doc.get("spec") == "# Spec\n实现 LRU"
        assert doc.get("tech_design") == "# 技术规范\n文件: lru.py"
        assert doc.get("implementation")
        assert "sdlc/test-sess/code/lru.py" in doc.get("code_paths", [])


# ============================================================
# 反馈回路：一次遇阻 → 解决
# ============================================================


class TestOneFeedbackResolved:
    async def test_first_attempt_blocked_second_resolved(self) -> None:
        """MiniMax 第 1 次遇阻 → GLM 反馈 → 第 2 次通过。"""
        mock_send = _make_send(
            deepseek="# Spec",
            glm_spec="# 技术规范",
            minimax_seq=[
                f"{NEED_HELP_MARKER} 不确定用 OrderedDict 还是 dict",
                "已按 GLM 指导实现，pytest 全过\n[FILES_WRITTEN] code/lru.py",
            ],
            glm_feedback_seq=["用 OrderedDict，move_to_end 更直观"],
        )
        with patch.object(a2a_client, "message_send", mock_send):
            graph = build_sdlc_graph()
            result = await graph.ainvoke(_state())

        assert result["feedback_rounds"] == 1
        feedbacks = result.get("sdlc_feedback", [])
        assert len(feedbacks) == 1
        assert feedbacks[0]["round"] == 1
        assert "OrderedDict" in feedbacks[0]["blocker"]


# ============================================================
# 反馈回路：达上限终止
# ============================================================


class TestMaxRoundsExhausted:
    async def test_blocked_at_max_rounds(self) -> None:
        """MiniMax 永远遇阻 → 卡在 MAX_FEEDBACK_ROUNDS → 终止。"""
        mock_send = _make_send(
            deepseek="# Spec",
            glm_spec="# 技术规范",
            minimax_seq=[
                f"{NEED_HELP_MARKER} 还是搞不定",
                f"{NEED_HELP_MARKER} 还是搞不定",
                f"{NEED_HELP_MARKER} 还是搞不定",
            ],
            glm_feedback_seq=["再试一次", "再试两次"],
        )
        with patch.object(a2a_client, "message_send", mock_send):
            graph = build_sdlc_graph()
            result = await graph.ainvoke(_state())

        assert result["feedback_rounds"] == MAX_FEEDBACK_ROUNDS
        assert NEED_HELP_MARKER in result["sdlc_doc"]["implementation"]
