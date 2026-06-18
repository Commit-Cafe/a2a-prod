"""SDLC 研发协作工作流（P2.1）。

DeepSeek 产 Spec → GLM-5.2 产技术规范 → MiniMax 写码 + 自测 → 遇阻反馈 GLM。

本模块含：
- 常量（``MAX_FEEDBACK_ROUNDS`` / ``NEED_HELP_MARKER`` / 等，spec §3.4）
- 兜底校验工具（spec §4.4 三层防御之"节点层"）
- 5 个 LangGraph 节点（Task 5 追加）
- SDLC 子图组装 + ``workflow_execute`` 主图节点（Task 5 追加）

角色边界（spec §0）：
- DeepSeek：写 PRD/Spec，不写实现代码、不审代码
- GLM-5.2：写技术规范 / 方案 / 审查 / 给 MiniMax 编码指令；**严禁写实现代码**
- MiniMax：写代码 + 自跑 pytest；**严格遵循上游文档，禁自由发挥**；
  **遇阻 [NEED_HELP] 反馈 GLM，不自行解决**
"""

from __future__ import annotations

import os
import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ============================================================
# 常量（spec §3.4）
# ============================================================

# S7 修复（GLM 2026-06-18 review）：MAX_FEEDBACK_ROUNDS 改函数级 get_max_feedback_rounds()，
# 允许测试用 monkeypatch.setenv 动态隔离。模块级常量保留仅作向后兼容（默认 2）。
MAX_FEEDBACK_ROUNDS_DEFAULT: int = 2
MAX_FEEDBACK_ROUNDS: int = int(os.getenv("SDLC_MAX_FEEDBACK_ROUNDS", str(MAX_FEEDBACK_ROUNDS_DEFAULT)))


def get_max_feedback_rounds() -> int:
    """运行时读取 SDLC_MAX_FEEDBACK_ROUNDS env（每次调用都读，测试可 monkeypatch 隔离）。

    与模块级 MAX_FEEDBACK_ROUNDS 的区别：模块级常量在 import 时冻结，测试改了 env
    不会生效；本函数每次都读 env。业务代码（check_blocked / aggregator）应优先调用本函数。
    """
    try:
        return int(os.getenv("SDLC_MAX_FEEDBACK_ROUNDS", str(MAX_FEEDBACK_ROUNDS_DEFAULT)))
    except ValueError:
        # 防御：env 被设成非数字时退回默认（不抛，避免影响业务）
        logger.warning("sdlc_max_feedback_rounds_invalid_fallback", default=MAX_FEEDBACK_ROUNDS_DEFAULT)
        return MAX_FEEDBACK_ROUNDS_DEFAULT


# MiniMax 遇阻标记（节点硬校验识别）
NEED_HELP_MARKER: str = "[NEED_HELP]"

# MiniMax 落盘文件清单标记（强约定，便于解析）
FILES_WRITTEN_MARKER: str = "[FILES_WRITTEN]"

# GLM 兜底校验：代码块超过此行数视为"完整实现"，剥离
GLM_CODE_BLOCK_MAX_LINES: int = int(os.getenv("SDLC_GLM_CODE_MAX_LINES", "15"))

# 子图执行超时（5+ 次 LLM 调用 × 60s 上限）
SDLC_TIMEOUT_SECONDS: int = int(os.getenv("SDLC_TIMEOUT_SECONDS", "600"))


# ============================================================
# 兜底校验：剥离 GLM 超长代码块（spec §4.4）
# ============================================================

_CODE_BLOCK_RE = re.compile(
    r"```(?:python|py|go|java|js|ts|rust)?\n.*?\n```",
    re.DOTALL,
)


def _strip_oversized_code_from_glm(
    text: str,
    *,
    max_lines: int = GLM_CODE_BLOCK_MAX_LINES,
) -> str:
    """GLM 节点输出后调用：若代码块超过 max_lines，剥离并打 warning。

    保留短的类型签名 / 伪代码（行数 ≤ max_lines），剥离完整实现。
    被剥离的位置替换为：「（GLM 应委派 MiniMax 实现：<首行摘要>…）」。

    行数定义：代码块 fence 内的 ``\\n`` 计数（含开闭 fence 行）。
    """

    def _replace(m: re.Match[str]) -> str:
        block = m.group(0)
        line_count = block.count("\n")
        if line_count <= max_lines:
            return block  # 短块保留
        first_line = block.split("\n")[1][:60] if "\n" in block else ""
        logger.warning(
            "glm_code_block_stripped",
            lines=line_count,
            preview=first_line,
        )
        return f"（GLM 应委派 MiniMax 实现：{first_line}…）"

    return _CODE_BLOCK_RE.sub(_replace, text)


# ============================================================
# 兜底校验：提取 MiniMax [NEED_HELP] 阻塞（spec §4.4）
# ============================================================


def _extract_need_help(implementation: str) -> str | None:
    """从 MiniMax 输出提取 [NEED_HELP] 后的 blocker 描述。

    Returns:
        blocker 文本（取标记后到下一个空行或结尾，再 strip）；无标记或标记后第一
        段落为空（仅空白）→ None
    """
    if NEED_HELP_MARKER not in implementation:
        return None
    idx = implementation.index(NEED_HELP_MARKER)
    rest = implementation[idx + len(NEED_HELP_MARKER) :]
    # 先按空行切段，取第一段，再 strip；空段落视为无效阻塞
    first_para = rest.split("\n\n")[0].strip()
    return first_para or None


# ============================================================
# 兜底校验：检测 MiniMax 自由发挥（spec §4.4）
# ============================================================

_FREESTYLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"我额外(加|实现|新增)"),
    re.compile(r"我觉得(应该|可以)"),
    re.compile(r"我建议(增加|加|用)"),
    re.compile(r"我自己(加|实现|改)"),
    re.compile(r"自行(新增|添加|决定)"),
)


def _detect_freestyle(implementation: str) -> str | None:
    """检测 MiniMax 自由发挥语义（违反"严格遵循上游"约束）。

    Returns:
        含上下文的问题描述（用于反馈给 GLM）；合规返回 None
    """
    for pattern in _FREESTYLE_PATTERNS:
        m = pattern.search(implementation)
        if m:
            start = max(0, m.start() - 10)
            end = min(len(implementation), m.end() + 40)
            return f"检测到自由发挥：...{implementation[start:end]}..."
    return None


# ============================================================
# 兜底校验：MiniMax 落盘文件路径提取 + 归一化（spec §4.4）
# ============================================================


def _normalize_code_path(p: str, session_id: str) -> str | None:
    """把 MiniMax 写的路径归一化为 ``sdlc/<sid>/code/`` 前缀。

    返回路径基准：相对 workspace 根（如 ``sdlc/sess1/code/lru.py``）。
    """
    p = p.strip().strip("`")
    if not p:
        return None
    prefix = f"sdlc/{session_id}/code/"
    if p.startswith(prefix) or p.startswith(f"/{prefix}"):
        return p.lstrip("/")
    if p.startswith("code/"):
        return prefix + p[len("code/") :]
    return prefix + p.lstrip("/")


def _extract_files_written(implementation: str, session_id: str) -> list[str]:
    """从 MiniMax 输出提取落盘文件路径。

    策略（a）``[FILES_WRITTEN]`` 标记提取 → 失败回退策略（b）目录扫描。
    返回路径基准：相对 workspace 根（如 ``sdlc/<sid>/code/lru.py``）。
    """
    from orchestrator.sdlc_workspace import WORKSPACE_SDLC_DIR  # noqa: PLC0415

    # (a) 强约定标记
    if FILES_WRITTEN_MARKER in implementation:
        idx = implementation.index(FILES_WRITTEN_MARKER)
        rest = implementation[idx + len(FILES_WRITTEN_MARKER) :]
        line = rest.split("\n")[0].strip()
        raw_paths = [p.strip() for p in line.split(",") if p.strip()]
        if raw_paths:
            normalized = [_normalize_code_path(p, session_id) for p in raw_paths]
            return [p for p in normalized if p]
    # (b) 兜底：扫描 workspace/sdlc/<sid>/code/
    code_dir = WORKSPACE_SDLC_DIR / session_id / "code"
    if code_dir.exists():
        # as_posix() 强制正斜杠，保证跨平台路径基准一致（spec §4.4）
        return [
            p.relative_to(WORKSPACE_SDLC_DIR.parent).as_posix()
            for p in code_dir.rglob("*")
            if p.is_file()
        ]
    return []


# ============================================================
# LangGraph 节点 + 子图（Task 5）
# ============================================================

import asyncio  # noqa: E402

from langgraph.graph import END, START, StateGraph  # noqa: E402

from observability.tracing import trace_node  # noqa: E402
from orchestrator import a2a_client, sdlc_prompts, sdlc_workspace  # noqa: E402
from orchestrator.executor import get_agent_url  # noqa: E402
from orchestrator.state import (  # noqa: E402
    AgentName,
    OrchestrationState,
    SdlcDoc,
    SdlcFeedback,
)

# ============================================================
# Agent URL 辅助（复用 executor.get_agent_url）
# ============================================================


def _glm_url() -> str:
    return get_agent_url(AgentName.GLM.value)


def _deepseek_url() -> str:
    return get_agent_url(AgentName.DEEPSEEK.value)


def _minimax_url() -> str:
    return get_agent_url(AgentName.MINIMAX.value)


# ============================================================
# 节点 1：DeepSeek 产 Spec（spec §4.2）
# ============================================================


@trace_node(name="orchestrator.sdlc.deepseek_doc")
async def deepseek_doc(state: OrchestrationState) -> dict[str, Any]:
    """DeepSeek 产 PRD/Spec 文档，落盘 spec.md。"""
    user_query = state["user_query"]
    session_id = state["session_id"]

    prompt = sdlc_prompts.DEEPSEEK_SPEC_PROMPT.format(user_query=user_query)
    text = await a2a_client.message_send(_deepseek_url(), prompt, session_id=session_id)

    sdlc_workspace.write_sdlc_doc(session_id, "spec.md", text)

    old_doc: SdlcDoc = state.get("sdlc_doc", {}) or {}
    new_doc: SdlcDoc = {**old_doc, "spec": text}
    return {"sdlc_doc": new_doc, "workflow_status": "running"}


# ============================================================
# 节点 2：GLM 产技术规范（禁代码，spec §4.2 + §4.4）
# ============================================================


@trace_node(name="orchestrator.sdlc.glm_spec")
async def glm_spec(state: OrchestrationState) -> dict[str, Any]:
    """GLM-5.2 产技术规范 + 编码指令；剥离超长代码块；落盘 tech-design.md。"""
    user_query = state["user_query"]
    session_id = state["session_id"]
    spec = (state.get("sdlc_doc") or {}).get("spec", "")

    prompt = sdlc_prompts.GLM_TECH_DESIGN_PROMPT.format(spec=spec, user_query=user_query)
    text = await a2a_client.message_send(_glm_url(), prompt, session_id=session_id)

    text = _strip_oversized_code_from_glm(text)
    sdlc_workspace.write_sdlc_doc(session_id, "tech-design.md", text)

    old_doc: SdlcDoc = state.get("sdlc_doc", {}) or {}
    new_doc: SdlcDoc = {**old_doc, "tech_design": text}
    return {"sdlc_doc": new_doc}


# ============================================================
# 节点 3：MiniMax 写码 + 自测（遇阻 [NEED_HELP]，禁自由发挥）
# ============================================================


@trace_node(name="orchestrator.sdlc.minimax_code")
async def minimax_code(state: OrchestrationState) -> dict[str, Any]:
    """MiniMax 按技术规范写码 + 自跑 pytest；遇阻标 [NEED_HELP]。"""
    session_id = state["session_id"]
    doc = state.get("sdlc_doc") or {}
    tech_design = doc.get("tech_design", "")
    feedbacks = state.get("sdlc_feedback", []) or []

    # 拼 feedback_block：把历史 GLM 指导注入 prompt
    if feedbacks:
        feedback_block = "\n\n".join(
            f"第 {fb['round']} 轮 GLM 指导：{fb['guidance']}" for fb in feedbacks
        )
    else:
        feedback_block = "（首次执行，无历史反馈）"

    prompt = sdlc_prompts.MINIMAX_CODE_PROMPT.format(
        tech_design=tech_design,
        session_id=session_id,
        feedback_block=feedback_block,
    )
    text = await a2a_client.message_send(_minimax_url(), prompt, session_id=session_id)

    code_paths = _extract_files_written(text, session_id)
    old_doc: SdlcDoc = state.get("sdlc_doc", {}) or {}
    new_doc: SdlcDoc = {**old_doc, "implementation": text, "code_paths": code_paths}
    return {"sdlc_doc": new_doc}


# ============================================================
# 节点 4：纯路由 check_blocked（spec §4.2）
# ============================================================


def check_blocked(state: OrchestrationState) -> str:
    """conditional_edges 路由：判定走 glm_feedback 还是 done。

    遇阻判定：显式 ``[NEED_HELP]`` 标记 OR 自由发挥被检测（spec §0 MiniMax 约束）。

    Returns:
        ``"glm_feedback"``（继续反馈）或 ``"done"``（进 aggregate）
    """
    impl = (state.get("sdlc_doc") or {}).get("implementation", "")
    rounds = state.get("feedback_rounds", 0)
    blocked = NEED_HELP_MARKER in impl or _detect_freestyle(impl) is not None
    # S7 修复：使用函数级 getter，使测试 monkeypatch.setenv 生效
    if blocked and rounds < get_max_feedback_rounds():
        return "glm_feedback"
    return "done"


# ============================================================
# 节点 5：GLM 给指导（禁代码，spec §4.2 + §4.4）
# ============================================================


@trace_node(name="orchestrator.sdlc.glm_feedback")
async def glm_feedback(state: OrchestrationState) -> dict[str, Any]:
    """GLM-5.2 给 MiniMax 指导（禁完整代码）；feedback_rounds += 1。"""
    session_id = state["session_id"]
    doc = state.get("sdlc_doc") or {}
    impl = doc.get("implementation", "")
    tech_design = doc.get("tech_design", "")
    old_feedbacks: list[SdlcFeedback] = state.get("sdlc_feedback", []) or []
    rounds = state.get("feedback_rounds", 0)

    # 提取 blocker：优先 [NEED_HELP]，其次自由发挥描述
    blocker = _extract_need_help(impl) or _detect_freestyle(impl) or "（未识别的阻塞）"
    prior_feedback = (
        "\n".join(f"第 {fb['round']} 轮：{fb['guidance']}" for fb in old_feedbacks) or "（无）"
    )

    prompt = sdlc_prompts.GLM_FEEDBACK_PROMPT.format(
        blocker=blocker,
        tech_design=tech_design,
        prior_feedback=prior_feedback,
    )
    guidance = await a2a_client.message_send(_glm_url(), prompt, session_id=session_id)
    guidance = _strip_oversized_code_from_glm(guidance)

    new_feedback: SdlcFeedback = {
        "round": rounds + 1,
        "blocker": blocker,
        "guidance": guidance,
    }
    return {
        "sdlc_feedback": [*old_feedbacks, new_feedback],
        "feedback_rounds": rounds + 1,
    }


# ============================================================
# SDLC 子图组装（spec §5.5）
# ============================================================


def build_sdlc_graph() -> Any:
    """构建 SDLC 子图（独立 compile，便于单元测试）。

    图结构（spec §2.1）：
        START → deepseek_doc → glm_spec → minimax_code
                                                ↓
                                          check_blocked ──┬─ glm_feedback → minimax_code（回路）
                                                          └─ END
    """
    sub = StateGraph(OrchestrationState)
    sub.add_node("deepseek_doc", deepseek_doc)
    sub.add_node("glm_spec", glm_spec)
    sub.add_node("minimax_code", minimax_code)
    sub.add_node("glm_feedback", glm_feedback)

    sub.add_edge(START, "deepseek_doc")
    sub.add_edge("deepseek_doc", "glm_spec")
    sub.add_edge("glm_spec", "minimax_code")

    sub.add_conditional_edges(
        "minimax_code",
        check_blocked,
        {
            "glm_feedback": "glm_feedback",
            "done": END,
        },
    )

    sub.add_edge("glm_feedback", "minimax_code")  # 反馈回路

    return sub.compile()


_COMPILED_SDLC_GRAPH: Any = None


def get_compiled_sdlc_graph() -> Any:
    """获取编译好的 SDLC 子图（惰性初始化，与 graph.py 风格一致）。"""
    global _COMPILED_SDLC_GRAPH
    if _COMPILED_SDLC_GRAPH is None:
        _COMPILED_SDLC_GRAPH = build_sdlc_graph()
    return _COMPILED_SDLC_GRAPH


# ============================================================
# 主图节点：workflow_execute（spec §5.5）
# ============================================================


async def workflow_execute(state: OrchestrationState) -> dict[str, Any]:
    """主图节点：跑 SDLC 子图，返回 partial state。

    主图的 ``workflow_execute`` 节点调用此函数；SDLC 业务逻辑全封装在子图里。
    """
    sub = get_compiled_sdlc_graph()
    result = await asyncio.wait_for(sub.ainvoke(state), timeout=SDLC_TIMEOUT_SECONDS)
    return dict(result)
