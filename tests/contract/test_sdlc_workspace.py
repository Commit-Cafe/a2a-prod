"""sdlc_workspace 落盘辅助契约测试（P2.1）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator import sdlc_workspace


@pytest.fixture
def temp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 WORKSPACE_SDLC_DIR 重定向到 tmp_path/sdlc，避免污染真实 workspace。"""
    target = tmp_path / "sdlc"
    monkeypatch.setattr(sdlc_workspace, "WORKSPACE_SDLC_DIR", target)
    return target


def test_write_sdlc_doc_creates_file(temp_workspace: Path) -> None:
    """write_sdlc_doc 创建 spec.md 文件。"""
    path = sdlc_workspace.write_sdlc_doc("sess-123", "spec.md", "# Spec content")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Spec content"


def test_write_sdlc_doc_creates_session_dir(temp_workspace: Path) -> None:
    """文件落在 <workspace>/sdlc/<session_id>/ 下。"""
    path = sdlc_workspace.write_sdlc_doc("sess-456", "tech-design.md", "# Tech")
    assert path.parent.name == "sess-456"
    assert path.parent.parent == temp_workspace


def test_write_sdlc_doc_overwrites_existing(temp_workspace: Path) -> None:
    """同名文件覆写（第二次写覆盖第一次）。"""
    sdlc_workspace.write_sdlc_doc("s1", "spec.md", "v1")
    sdlc_workspace.write_sdlc_doc("s1", "spec.md", "v2")
    path = temp_workspace / "s1" / "spec.md"
    assert path.read_text(encoding="utf-8") == "v2"


def test_write_sdlc_doc_multiple_sessions_isolated(temp_workspace: Path) -> None:
    """不同 session 落在不同目录。"""
    sdlc_workspace.write_sdlc_doc("s1", "spec.md", "from-s1")
    sdlc_workspace.write_sdlc_doc("s2", "spec.md", "from-s2")
    assert (temp_workspace / "s1" / "spec.md").read_text(encoding="utf-8") == "from-s1"
    assert (temp_workspace / "s2" / "spec.md").read_text(encoding="utf-8") == "from-s2"
