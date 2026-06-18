"""Classifier 节点：意图分类 + 路由（关键词启发式）。

输入：用户原始 query
输出：
- ``mode``：OrchestrationMode.DIRECT 或 OrchestrationMode.TASK_DECOMPOSITION
- ``target_agent``（DIRECT 模式）：选中的 Agent
- ``subtasks``（DECOMPOSITION 模式）：拆出的子任务列表

策略：纯关键词启发式（用户决策 A，P2.1 升级到 LLM Router）。

设计原则：
- **可替换**：本节点输出 schema 固定，未来换 LLM 路由时只换实现不换接口
- **可观测**：每次决策都打日志（reason 字段），便于 e2e 调试
- **可降级**：关键词全 miss 时 fallback 到 GLM（最通用 Agent）
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from observability.tracing import trace_node
from orchestrator.state import (
    AgentName,
    OrchestrationMode,
    OrchestrationState,
    SubTask,
)

logger = structlog.get_logger(__name__)


# ============================================================
# 关键词路由表（按 Agent 维度组织，对齐 SPEC §2.2 三 Agent 人设）
# ============================================================

# DeepSeek = 产品经理 + 技术总监：需求拆解、方案设计、技术选型、权衡决策
DEEPSEEK_KEYWORDS: tuple[str, ...] = (
    # 需求/产品
    "需求",
    "用户故事",
    "use case",
    "用例",
    "场景",
    # 方案/设计
    "方案",
    "设计",
    "选型",
    "架构",
    "技术方案",
    "概要设计",
    "详细设计",
    # 决策/权衡
    "权衡",
    "决策",
    "推荐",
    "tradeoff",
    "对比方案",
    # 分析/拆解
    "分析",
    "拆解",
    "评估",
    "可行性",
    "风险分析",
    "MVP",
    # 英语关键词
    "requirement",
    "solution",
    "architecture",
    "design",
)

# MiniMax = 程序员（主力代码编写）：编码、实现、重构、调试、测试
MINIMAX_KEYWORDS: tuple[str, ...] = (
    # 代码工程
    "代码",
    "编程",
    "重构",
    "调试",
    "测试",
    "单元测试",
    "pytest",
    "fixture",
    "工程",
    "实现",
    "实现一个",
    "写一个",
    "帮我写",
    "帮我把",
    "改造",
    # 工程实践
    "类型注解",
    "异常处理",
    "日志",
    "重试",
    "并发",
    "线程",
    "锁",
    # 语言/框架
    "python",
    "javascript",
    "typescript",
    "java",
    "go",
    "rust",
    "fastapi",
    "django",
    "flask",
    "react",
    "vue",
    # 算法实现（注意：纯算法分析归 DeepSeek，算法实现归 MiniMax）
    "算法实现",
    "排序算法",
    "查找算法",
    "动态规划",
    # 英语关键词
    "code",
    "coding",
    "refactor",
    "debug",
    "test",
    "implement",
)

# GLM = 技术总监 + 代码审查：审查、评估代码质量、识别风险
GLM_KEYWORDS: tuple[str, ...] = (
    # 审查/评审
    "审查",
    "评审",
    "code review",
    "review",
    "代码审查",
    # 质量/规范
    "质量",
    "规范",
    "最佳实践",
    "best practice",
    "代码风格",
    # 风险/安全
    "风险",
    "安全",
    "security",
    "漏洞",
    "注入",
    "越权",
    "敏感信息",
    # 改进/优化建议
    "改进",
    "优化建议",
    "重构建议",
)

# 任务分解触发词（含这些词时进入 DECOMPOSITION 模式）
DECOMPOSITION_KEYWORDS: tuple[str, ...] = (
    "对比",
    "比较",
    "区别",
    "分别",
    "同时",
    "另一方面",
    "和",
    "与",
    "以及",  # 注意：单字容易误判，下面用正则约束
    "compare",
    "contrast",
    "versus",
    "vs",
)

# 复合模式：用关键词 + 正则约束，避免"我和你"误触发
DECOMPOSITION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "对比/比较 A 和/与 B"
    re.compile(r"(对比|比较|分别).+(和|与|以及)"),
    # "A 和/与 B 的区别/差异/不同"
    re.compile(r".+(和|与|以及).+(区别|差异|不同)"),
    # "同时/分别" 表明要多个角度
    re.compile(r"(同时|分别|各自)"),
    # 英文 vs / compare
    re.compile(r"\b(compare|contrast|versus|vs\.?)\b", re.IGNORECASE),
)


# ============================================================
# WORKFLOW 触发（P2.1：SDLC 研发协作工作流，spec §5.1）
# ============================================================

# WORKFLOW 触发：用户表达"要一个完整研发流程"的语义
# 区别于 DIRECT（单点问答）和 DECOMPOSITION（多视角对比）
WORKFLOW_KEYWORDS: tuple[str, ...] = (
    # 端到端研发语义
    "实现一个需求",
    "做一个功能",
    "做一个 feature",
    "开发一个",
    "从需求到代码",
    "完整实现",
    # 交付物语义（暗示要 spec + 设计 + 代码）
    "交付一个",
    "产出一个",
    "帮我做一个",
)

# 硬关键词：含这些词无视长度直接触发（spec §5.1 显式工作流语义）
WORKFLOW_HARD_KEYWORDS: tuple[str, ...] = (
    "走工作流",
    "走流程",
    "sdlc",
    "研发流程",
    "端到端",
)

# 复合模式：正则约束（spec §5.1）
# - WORKFLOW_STRONG_PATTERNS：强语义，无视长度（"完整 + 研发动词"）
# - WORKFLOW_PATTERNS：弱语义，需 query ≥ 15 字（"实现/开发 + 名词"）
WORKFLOW_STRONG_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 显式 "完整" + 研发动词（强语义，无视长度）
    re.compile(r"完整.{0,10}(实现|开发|设计|流程)"),
)

WORKFLOW_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "实现/开发/做一个 <名词>" 且后续足够具体（受长度门槛约束）
    re.compile(r"(实现|开发|做一个|帮我做一个|交付).{2,30}"),
)


# ============================================================
# 主节点
# ============================================================


@trace_node(name="orchestrator.classify")
def classify(state: OrchestrationState) -> dict[str, Any]:
    """LangGraph 节点：分类 + 路由。

    返回 partial state（被 LangGraph merge 到完整 state）。

    B3 修复（GLM 2026-06-18 review）：当上游（OpenAI 兼容层等）已经显式设置了
    ``target_agent``（如 ``/v1/chat/completions model=glm-agent``），则跳过关键词分类，
    强制 DIRECT 路由；后续节点（如 ``direct_execute``）读 ``state["target_agent"]``。
    """
    query = state["user_query"]
    log = logger.bind(session_id=state.get("session_id", "-"), query_preview=query[:60])

    # 0) B3：上游强制路由（target_agent 已由 orchestrate() 注入）→ 短路
    forced_target = state.get("target_agent")
    if forced_target:
        log.info("classify_forced_direct", target_agent=forced_target, reason="forced_by_caller")
        return {
            "mode": OrchestrationMode.DIRECT.value,
            "target_agent": forced_target,
            "subtasks": [],  # 不使用
        }

    # 1) 先判断是否进入任务分解模式
    if _is_decomposition(query):
        subtasks = _decompose(query)
        log.info(
            "classify_decomposition",
            subtask_count=len(subtasks),
            subtasks=[(st.get("assigned_to"), st.get("description", "")[:30]) for st in subtasks],
            reason="decomposition_keyword_or_pattern",
        )
        return {
            "mode": OrchestrationMode.TASK_DECOMPOSITION.value,
            "subtasks": subtasks,
            "target_agent": "",  # 不使用
        }

    # 2) 【P2.1】WORKFLOW 模式：SDLC 研发协作工作流（spec §5.2）
    #    判定优先级：DECOMPOSITION > WORKFLOW > DIRECT
    #    （必须先 WORKFLOW 后 DIRECT，否则"实现一个 X"会被 MiniMax 关键词抢走）
    if _is_workflow(query):
        log.info("classify_workflow", reason="workflow_keyword_or_pattern")
        return {
            "mode": OrchestrationMode.WORKFLOW.value,
            "target_agent": "",  # 不使用
            "subtasks": [],  # 不使用
            "workflow_status": "running",
            "feedback_rounds": 0,
        }

    # 3) 单任务直接路由：按关键词匹配选 Agent
    target, reason = _select_agent_by_keyword(query)
    log.info("classify_direct", target_agent=target, reason=reason)

    return {
        "mode": OrchestrationMode.DIRECT.value,
        "target_agent": target,
        "subtasks": [],  # 不使用
    }


# ============================================================
# 内部工具
# ============================================================


def _is_decomposition(query: str) -> bool:
    """判断是否需要任务分解。"""
    # 1) 强模式：正则匹配（精准）
    for pattern in DECOMPOSITION_PATTERNS:
        if pattern.search(query):
            return True
    # 2) 弱模式：关键词 + 长度约束（避免 "我和你" 误判）
    if len(query) >= 10:
        for kw in ("对比", "比较", "区别", "分别"):
            if kw in query:
                return True
    return False


def _is_workflow(query: str) -> bool:
    """判断是否进入 SDLC WORKFLOW 模式（spec §5.1）。

    判定优先级（任一命中即触发）：
    1. **硬关键词**：含"走工作流/sdlc/研发流程/端到端"等显式工作流语义（无视长度）
    2. **强正则**：``完整 + 研发动词``（强语义，无视长度）
    3. **弱模式**：query ≥ 15 字 且（弱正则 OR 弱关键词）命中

    为什么弱正则也走长度门槛：``WORKFLOW_PATTERNS[0]``（``(实现|开发|...).{2,30}``）
    在短句如"实现一个 hello"上也会匹配，必须用长度门槛过滤短句。

    返回 True 时进 WORKFLOW；返回 False 时降级到 DIRECT。
    """
    # 1) 硬关键词：显式工作流语义，无视长度
    for kw in WORKFLOW_HARD_KEYWORDS:
        if kw in query:
            return True
    # 2) 强正则：无视长度
    for pattern in WORKFLOW_STRONG_PATTERNS:
        if pattern.search(query):
            return True
    # 3) 弱模式：长句（≥15 字）才走弱正则 + 弱关键词
    if len(query) >= 15:
        for pattern in WORKFLOW_PATTERNS:
            if pattern.search(query):
                return True
        for kw in WORKFLOW_KEYWORDS:
            if kw in query:
                return True
    return False


def _decompose(query: str) -> list[SubTask]:
    """任务分解：把 query 拆成多个子任务（每个发给不同 Agent）。

    本阶段策略（启发式）：
    - 识别"对比/比较 X 和 Y"模式 → 把 X 和 Y 分别发给两个不同 Agent
    - 识别"分别 ... A、B、C"模式 → 每个发给一个 Agent
    - 兜底：把整个 query 发给 3 个 Agent，让他们各自作答（aggregate 时合并）

    注：P2.1 升级 LLM 路由后，这里换成 LLM 拆解。
    """
    # 简单启发式：兜底广播模式（三个 Agent 都答，aggregate 时综合）
    # 这样能展示多 Agent 协作，且不依赖复杂 NLP
    return [
        SubTask(
            description=f"从需求拆解与技术方案角度回答：{query}",
            assigned_to=AgentName.DEEPSEEK.value,
        ),
        SubTask(
            description=f"从代码实现与工程落地角度回答：{query}",
            assigned_to=AgentName.MINIMAX.value,
        ),
        SubTask(
            description=f"从代码审查与质量把关角度回答：{query}",
            assigned_to=AgentName.GLM.value,
        ),
    ]


def _select_agent_by_keyword(query: str) -> tuple[str, str]:
    """根据关键词选 Agent。

    匹配策略：**跨 Agent 按关键词长度降序匹配**（长关键词优先）。

    为什么不按 Agent 优先级顺序匹配？
    → 因为 GLM_KEYWORDS 里有 "代码审查"（4字），MINIMAX_KEYWORDS 里有 "代码"（2字）。
      如果 MiniMax 先检查，"代码审查" 永远会被 "代码" 抢走 → GLM 永远路由不到。
      长关键词优先可保证 "代码审查" → GLM，"代码" → MiniMax 各得其所。

    长度相同时，按 Agent 优先级：DeepSeek > MiniMax > GLM（数字越小越优先）。

    Returns:
        (agent_name, reason) 元组，便于日志追踪
    """
    query_lower = query.lower()

    # 收集所有 (keyword, agent_priority) 对
    # agent_priority: 0=DeepSeek, 1=MiniMax, 2=GLM
    _priority_map = {
        AgentName.DEEPSEEK.value: 0,
        AgentName.MINIMAX.value: 1,
        AgentName.GLM.value: 2,
    }
    all_pairs: list[tuple[str, str, int]] = [
        *[
            (kw, AgentName.DEEPSEEK.value, _priority_map[AgentName.DEEPSEEK.value])
            for kw in DEEPSEEK_KEYWORDS
        ],
        *[
            (kw, AgentName.MINIMAX.value, _priority_map[AgentName.MINIMAX.value])
            for kw in MINIMAX_KEYWORDS
        ],
        *[(kw, AgentName.GLM.value, _priority_map[AgentName.GLM.value]) for kw in GLM_KEYWORDS],
    ]
    # 排序：先按长度降序（负号），再按 agent 优先级升序
    all_pairs.sort(key=lambda x: (-len(x[0]), x[2]))

    for kw, agent, _ in all_pairs:
        if kw.lower() in query_lower:
            return agent, f"keyword:{kw}"

    # 全部 miss → GLM 兜底
    return AgentName.GLM.value, "fallback:no_keyword_match"


# ============================================================
# LangGraph 路由函数（conditional_edges 用）
# ============================================================


def route_after_classify(state: OrchestrationState) -> str:
    """conditional_edges 路由：classify 后决定走 direct / decompose / workflow。

    Returns:
        "direct" / "decompose" / "workflow"（对应 graph 节点名）
    """
    mode = state.get("mode", "")
    if mode == OrchestrationMode.TASK_DECOMPOSITION.value:
        return "decompose"
    if mode == OrchestrationMode.WORKFLOW.value:  # P2.1
        return "workflow"
    return "direct"
