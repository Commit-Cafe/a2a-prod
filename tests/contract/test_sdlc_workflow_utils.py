"""sdlc_workflow 兜底校验工具契约测试（P2.1）。

测常量 + 5 个工具函数（spec §4.4）：
- ``_strip_oversized_code_from_glm``：剥离 GLM 超长代码块
- ``_extract_need_help``：提取 MiniMax [NEED_HELP] 阻塞
- ``_detect_freestyle``：检测 MiniMax 自由发挥
- ``_normalize_code_path`` / ``_extract_files_written``：落盘文件路径归一化与提取
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from orchestrator.sdlc_workflow import (
    GLM_CODE_BLOCK_MAX_LINES,
    MAX_FEEDBACK_ROUNDS,
    NEED_HELP_MARKER,
    _detect_freestyle,
    _extract_files_written,
    _extract_need_help,
    _normalize_code_path,
    _strip_oversized_code_from_glm,
    get_max_feedback_rounds,
)

# ============================================================
# 常量
# ============================================================


def test_constants_have_expected_defaults() -> None:
    assert NEED_HELP_MARKER == "[NEED_HELP]"
    assert MAX_FEEDBACK_ROUNDS == 2
    assert GLM_CODE_BLOCK_MAX_LINES == 15


# ============================================================
# _strip_oversized_code_from_glm
# ============================================================


class TestStripOversizedCode:
    def test_strips_oversized_block(self) -> None:
        """超过 max_lines 的代码块被剥离，含'应委派'提示。"""
        big_block = "```python\n" + "\n".join(f"x{i} = 1" for i in range(30)) + "\n```"
        result = _strip_oversized_code_from_glm(big_block)
        assert "应委派 MiniMax 实现" in result
        assert "x29 = 1" not in result

    def test_keeps_short_signature(self) -> None:
        """短代码块（类型签名 / 伪代码，行数 ≤ max_lines）保留。"""
        short = "# 接口\n```python\ndef foo(x: int) -> str: ...\n```"
        assert _strip_oversized_code_from_glm(short) == short

    def test_keeps_under_threshold(self) -> None:
        """行数严格小于 max_lines 的块保留（13 行内容 → 14 个 \\n → 保留）。"""
        block = "```python\n" + "\n".join(f"l{i}" for i in range(13)) + "\n```"
        assert _strip_oversized_code_from_glm(block) == block

    def test_no_code_block_passthrough(self) -> None:
        """无代码块的纯文档原样返回。"""
        text = "# 标题\n纯文字说明，无代码。"
        assert _strip_oversized_code_from_glm(text) == text

    def test_mixed_blocks_selective_strip(self) -> None:
        """混合：短块保留，长块剥离。"""
        text = (
            "```python\ndef sig(x: int) -> str: ...\n```\n"
            "说明文字\n"
            "```python\n" + "\n".join(f"y{i}=1" for i in range(20)) + "\n```"
        )
        result = _strip_oversized_code_from_glm(text)
        assert "def sig" in result  # 短块保留
        assert "应委派 MiniMax 实现" in result  # 长块剥离
        assert "y19=1" not in result


# ============================================================
# _extract_need_help
# ============================================================


class TestExtractNeedHelp:
    def test_extracts_after_marker(self) -> None:
        text = "[NEED_HELP] 不确定用 OrderedDict 还是 dict\n\n其他内容"
        assert _extract_need_help(text) == "不确定用 OrderedDict 还是 dict"

    def test_returns_none_when_no_marker(self) -> None:
        assert _extract_need_help("已实现，pytest 全过") is None

    def test_extracts_to_end_when_no_blank_line(self) -> None:
        text = "[NEED_HELP] 单行问题"
        assert _extract_need_help(text) == "单行问题"

    def test_returns_none_for_empty_marker(self) -> None:
        """标记后内容为空 → None（无效阻塞）。"""
        assert _extract_need_help("[NEED_HELP] \n\n其他") is None


# ============================================================
# _detect_freestyle
# ============================================================


class TestDetectFreestyle:
    @pytest.mark.parametrize(
        "text",
        [
            "我额外加了一个 cache_size 参数",
            "我觉得应该用 Redis 替代内存缓存",
            "我建议增加一个 TTL 功能",
            "我自己加了一个 helper 函数",
            "自行新增了一个 utils.py",
        ],
    )
    def test_freestyle_detected(self, text: str) -> None:
        assert _detect_freestyle(text) is not None

    def test_compliant_output_not_flagged(self) -> None:
        compliant = "已按技术规范实现 lru.py，导出 LRUCache 类，附 5 个 pytest 用例，全过"
        assert _detect_freestyle(compliant) is None

    def test_freestyle_returns_context(self) -> None:
        """返回值含上下文片段（用于反馈给 GLM）。"""
        result = _detect_freestyle("实现完成，我额外加了 TTL 支持")
        assert result is not None
        assert "TTL" in result or "额外" in result


# ============================================================
# _normalize_code_path
# ============================================================


class TestNormalizeCodePath:
    def test_already_prefixed(self) -> None:
        assert _normalize_code_path("sdlc/sess1/code/lru.py", "sess1") == "sdlc/sess1/code/lru.py"

    def test_code_prefix_only(self) -> None:
        assert _normalize_code_path("code/lru.py", "sess1") == "sdlc/sess1/code/lru.py"

    def test_bare_filename(self) -> None:
        assert _normalize_code_path("lru.py", "sess1") == "sdlc/sess1/code/lru.py"

    def test_strips_backticks(self) -> None:
        assert _normalize_code_path("`code/lru.py`", "sess1") == "sdlc/sess1/code/lru.py"

    def test_empty_returns_none(self) -> None:
        assert _normalize_code_path("", "sess1") is None
        assert _normalize_code_path("   ", "sess1") is None


# ============================================================
# _extract_files_written
# ============================================================


class TestExtractFilesWritten:
    def test_extracts_from_marker(self) -> None:
        """[FILES_WRITTEN] 标记存在 → 提取路径（归一化）。"""
        text = "实现完成\n[FILES_WRITTEN] code/lru.py, code/test_lru.py"
        paths = _extract_files_written(text, "sess1")
        assert paths == ["sdlc/sess1/code/lru.py", "sdlc/sess1/code/test_lru.py"]

    def test_extracts_from_marker_full_path(self) -> None:
        """标记里写完整路径也能正确识别（不重复加前缀）。"""
        text = "done\n[FILES_WRITTEN] sdlc/sess2/code/a.py"
        assert _extract_files_written(text, "sess2") == ["sdlc/sess2/code/a.py"]

    def test_fallback_to_dir_scan(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """无标记 → 扫描 workspace/sdlc/<sid>/code/ 目录。"""
        from orchestrator import sdlc_workspace

        code_dir = tmp_path / "sdlc" / "sess2" / "code"
        code_dir.mkdir(parents=True)
        (code_dir / "a.py").write_text("x=1", encoding="utf-8")
        (code_dir / "b.py").write_text("y=2", encoding="utf-8")
        monkeypatch.setattr(sdlc_workspace, "WORKSPACE_SDLC_DIR", tmp_path / "sdlc")

        paths = _extract_files_written("实现完成，无标记", "sess2")
        assert sorted(paths) == ["sdlc/sess2/code/a.py", "sdlc/sess2/code/b.py"]

    def test_no_files_returns_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """无标记且目录不存在 → 空列表。"""
        from orchestrator import sdlc_workspace

        monkeypatch.setattr(sdlc_workspace, "WORKSPACE_SDLC_DIR", tmp_path / "sdlc")
        paths = _extract_files_written("无标记", "nonexistent-session")
        assert paths == []


# ============================================================
# S7 修复（GLM 2026-06-18 review）：get_max_feedback_rounds 函数级
# ============================================================


class TestGetMaxFeedbackRounds:
    """S7：函数级 getter 允许 monkeypatch.setenv 隔离。

    旧 MAX_FEEDBACK_ROUNDS 模块级常量在 import 时冻结 env，测试改了 env 不会生效。
    修复后 get_max_feedback_rounds() 每次调用读最新 env。
    """

    def test_default_is_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SDLC_MAX_FEEDBACK_ROUNDS", raising=False)
        assert get_max_feedback_rounds() == 2

    def test_env_override_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SDLC_MAX_FEEDBACK_ROUNDS", "1")
        assert get_max_feedback_rounds() == 1

    def test_env_override_3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SDLC_MAX_FEEDBACK_ROUNDS", "3")
        assert get_max_feedback_rounds() == 3

    def test_invalid_env_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """env 被设成非数字时退回 2，不抛。"""
        monkeypatch.setenv("SDLC_MAX_FEEDBACK_ROUNDS", "not-a-number")
        assert get_max_feedback_rounds() == 2

    def test_module_constant_still_works(self) -> None:
        """向后兼容：MAX_FEEDBACK_ROUNDS 模块常量仍可被 import（不破坏现有测试）。"""
        assert isinstance(MAX_FEEDBACK_ROUNDS, int)
        assert MAX_FEEDBACK_ROUNDS >= 1

    def test_check_blocked_uses_dynamic_value(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S7 集成：check_blocked 读 get_max_feedback_rounds 而非模块常量。

        当 rounds=0 且被 blocked 时，env=1 → 应进入 glm_feedback；
        旧实现因模块常量冻结，会用 2 判断（这里 rounds=0 < 2 也走 glm_feedback，但语义不同）。
        用 rounds=1, env=1, blocked=True → 应 done（达上限）。
        """
        from orchestrator.sdlc_workflow import NEED_HELP_MARKER, check_blocked
        from orchestrator.state import OrchestrationState

        monkeypatch.setenv("SDLC_MAX_FEEDBACK_ROUNDS", "1")
        state = cast(
            OrchestrationState,
            {
                "sdlc_doc": {"implementation": f"前文 {NEED_HELP_MARKER} 阻塞描述"},
                "feedback_rounds": 1,  # 已达上限
            },
        )
        assert check_blocked(state) == "done"

        # 还原：rounds=0, env=1 → 应进 glm_feedback
        state["feedback_rounds"] = 0
        assert check_blocked(state) == "glm_feedback"
