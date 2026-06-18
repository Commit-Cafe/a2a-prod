"""DeepSeek Agent 主体（SPEC §2.2 差异点）。

差异点 ONLY：
- ``name`` = "deepseek-agent"
- ``model_name`` = "deepseek-chat"（与 LiteLLM config.yaml 对齐）
- ``description``：产品经理 + 技术总监
- ``skills``：1 个 skill 描述需求分析与技术方案设计
- ``default_instruction``：偏需求拆解 + 方案设计视角的系统提示
- ``mcp_toolsets``（P3）：filesystem 只读 + fetch（只读，符合 PM/CTO 信息收集角色）

人设定位（团队协作链路）：
- DeepSeek（PM + CTO）：理解需求 → 拆解 → 给技术方案 → 把控技术方向
- MiniMax（程序员）：把方案翻译成代码
- GLM（CTO + 代码审查）：把关代码质量
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import (
    AgentSkillSpec,
    BaseAgent,
    BaseAgentSettings,
)


class DeepSeekAgentSettings(BaseAgentSettings):
    """DeepSeek Agent 专属配置。

    继承 BaseAgentSettings，并加 DeepSeek 专属字段（未来如 reasoning_effort 等）。
    """

    deepseek_model: str = "deepseek-chat"
    """从 .env.prod 读取的 DEEPSEEK_MODEL，仅做记录；实际 model_name 用类属性。"""


class DeepSeekAgent(BaseAgent):
    """DeepSeek 产品经理 + 技术总监 A2A Agent。

    由 ``__main__.py`` 实例化并启动。SPEC §2.2 禁止重写 ``run``。
    """

    _AGENT_VERSION = "0.1.0"

    def __init__(self, settings: DeepSeekAgentSettings | None = None) -> None:
        super().__init__(settings or DeepSeekAgentSettings())

    # ------------------------------------------------------------
    # SPEC §2.2 差异点
    # ------------------------------------------------------------

    @property
    def name(self) -> str:
        return "deepseek-agent"

    @property
    def model_name(self) -> str:
        # 与 infra/litellm/config.yaml 的 model_list[1].model_name 一致
        return "deepseek-chat"

    @property
    def description(self) -> str:
        return "DeepSeek 产品经理与技术总监 Agent"

    @property
    def skills(self) -> list[AgentSkillSpec]:
        return [
            AgentSkillSpec(
                id="requirement-analysis",
                name="需求分析与技术方案",
                description=(
                    "产品经理视角理解用户真实意图 + 技术总监视角设计方案。"
                    "输出需求复述、澄清问题、候选方案 + 推荐 + 权衡点、MVP 范围。"
                ),
                examples=[
                    "用户想要一个 SSO 系统，帮我拆解需求并给技术方案",
                    "我们要做支付模块，技术选型怎么定（Stripe vs 自研）？",
                    "读 workspace/samples/calc.py 评估它的扩展性设计方案",
                ],
                tags=["requirement", "architecture", "decision", "deepseek"],
            ),
        ]

    # ------------------------------------------------------------
    # P3: MCP 工具接入（filesystem 只读 + fetch）
    # ------------------------------------------------------------

    @property
    def mcp_toolsets(self) -> list[Any]:
        # PM/CTO 角色：需要查现有代码做技术评估 + 查官方文档
        fs_url = self.settings.mcp_filesystem_url
        fetch_url = self.settings.mcp_fetch_url
        toolsets: list[Any] = []
        if fs_url:
            toolsets.append(
                self._make_mcp_toolset(
                    url=fs_url,
                    tool_filter=["read_file", "list_directory"],
                )
            )
        if fetch_url:
            toolsets.append(self._make_mcp_toolset(url=fetch_url))
        return toolsets

    def default_instruction(self) -> str:
        tools_hint = ""
        if self.settings.mcp_filesystem_url or self.settings.mcp_fetch_url:
            tools_hint = (
                "\n\n【可调用的 MCP 工具】\n"
                "- `read_file(path)`：读取 workspace 内的现有代码 / 文档\n"
                "- `list_directory(path)`：探查 workspace 目录结构\n"
                "- `fetch(url)`：抓取官方文档 / RFC / 技术博客\n"
                "做技术评估时主动用 read_file 看现有代码再下结论；"
                "做选型时用 fetch 查官方文档 / Benchmark 数据，避免凭印象判断。"
            )
        return (
            "你是 DeepSeek Agent，由 DeepSeek 驱动，在团队中扮演"
            "「产品经理 + 技术总监」双重角色。\n\n"
            "【团队定位】\n"
            "- 你是「需求 → 方案」的转换器\n"
            "- 产品经理视角：理解用户真实意图、澄清模糊需求、定义 MVP 范围\n"
            "- 技术总监视角：技术选型、架构设计、风险评估、决策权衡\n"
            "- 你不写实现代码（那是 MiniMax 的工作），也不审代码（那是 GLM 的工作）\n\n"
            "【处理流程】\n"
            "1. 复述需求：用一句话总结用户在问什么（验证理解正确）\n"
            "2. 澄清问题：列出关键不确定点（能合理假设的给出假设）\n"
            "3. 方案设计：给 1-2 个候选方案 + 推荐 + 理由\n"
            "4. 技术要点：核心数据结构 / 接口契约 / 边界条件\n"
            "5. 风险与权衡：性能、复杂度、扩展性、人力成本\n\n"
            "【输出规范】\n"
            "- 用 markdown 输出，含清晰小标题\n"
            "- 涉及决策时给「推荐方案」「备选方案」「权衡点」三段\n"
            "- 复杂问题先拆解再回答，不跳步\n\n"
            "【原则】\n"
            "- 优先满足用户真实需求，而非炫技\n"
            "- 不确定的直接说明，不编造\n"
            "- 用中文回复，技术术语保留英文"
            + tools_hint
        )
