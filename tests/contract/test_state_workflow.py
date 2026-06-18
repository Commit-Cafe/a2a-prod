"""State schema 扩展契约测试（P2.1 WORKFLOW 模式）。"""

from __future__ import annotations

from orchestrator.state import (
    OrchestrationMode,
    OrchestrationState,
    SdlcDoc,
    SdlcFeedback,
)


def test_workflow_mode_enum_exists() -> None:
    """OrchestrationMode 必须含 WORKFLOW 成员。"""
    assert OrchestrationMode.WORKFLOW.value == "workflow"


def test_sdlc_doc_typeddict_keys() -> None:
    """SdlcDoc 含 spec / tech_design / implementation / code_paths 四键。"""
    doc: SdlcDoc = {
        "spec": "# Spec",
        "tech_design": "# Tech",
        "implementation": "# Impl",
        "code_paths": ["sdlc/sid/code/lru.py"],
    }
    assert doc["spec"] == "# Spec"
    assert doc["code_paths"] == ["sdlc/sid/code/lru.py"]


def test_sdlc_feedback_typeddict_keys() -> None:
    """SdlcFeedback 含 round / blocker / guidance 三键。"""
    fb: SdlcFeedback = {"round": 1, "blocker": "不确定", "guidance": "用 OrderedDict"}
    assert fb["round"] == 1


def test_orchestration_state_accepts_workflow_fields() -> None:
    """OrchestrationState 必须接受 WORKFLOW 专用字段。"""
    state: OrchestrationState = {
        "user_query": "实现一个 LRU",
        "session_id": "s1",
        "mode": OrchestrationMode.WORKFLOW.value,
        "sdlc_doc": {"spec": "# S"},
        "sdlc_feedback": [],
        "feedback_rounds": 0,
        "workflow_status": "running",
    }
    assert state["mode"] == "workflow"
    assert state["feedback_rounds"] == 0
