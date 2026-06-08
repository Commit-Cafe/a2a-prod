"""GLM Agent 主体（SPEC §2.2 差异点）。

差异点 ONLY：
- ``name`` = "glm-agent"
- ``model_name`` = "glm-5.1"（与 LiteLLM config.yaml 对齐）
- ``description``：技术总监 + 代码审查
- ``skills``：1 个 skill 描述代码评审与质量把关
- ``default_instruction``：偏代码审查视角的系统提示
- ``mcp_toolsets``（P3）：filesystem 只读 + fetch（只读权限，符合审查角色）

人设定位（团队协作链路）：
- DeepSeek（PM + CTO）：需求拆解 → 技术方案
- MiniMax（程序员）：把方案翻译成代码
- GLM（CTO + 代码审查）：把关代码质量、识别风险、给改进建议
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import (
    AgentSkillSpec,
    BaseAgent,
    BaseAgentSettings,
)


class GLMAgentSettings(BaseAgentSettings):
    """GLM Agent 专属配置。

    继承 BaseAgentSettings，并加 GLM 专属字段（未来如 top_p / temperature 等）。
    """

    glm_model: str = "glm-5.1"
    """从 .env.prod 读取的 GLM_MODEL，仅做记录；实际 model_name 用类属性。"""


class GLMAgent(BaseAgent):
    """GLM-5.1 技术总监 + 代码审查 A2A Agent。

    由 ``__main__.py`` 实例化并启动。SPEC §2.2 禁止重写 ``run``。
    """

    _AGENT_VERSION = "0.1.0"

    def __init__(self, settings: GLMAgentSettings | None = None) -> None:
        super().__init__(settings or GLMAgentSettings())

    # ------------------------------------------------------------
    # SPEC §2.2 差异点
    # ------------------------------------------------------------

    @property
    def name(self) -> str:
        return "glm-agent"

    @property
    def model_name(self) -> str:
        # 与 infra/litellm/config.yaml 的 model_list[0].model_name 一致
        return "glm-5.1"

    @property
    def description(self) -> str:
        return "GLM-5.1 技术总监与代码审查 Agent"

    @property
    def skills(self) -> list[AgentSkillSpec]:
        return [
            AgentSkillSpec(
                id="code-review",
                name="代码审查与质量把关",
                description=(
                    "站在技术总监视角评审代码：正确性、可读性、安全性、性能、可维护性。"
                    "输出三级问题清单（阻塞/建议/优化）+ 可落地的改进建议。"
                ),
                examples=[
                    "审查这段 Python 代码：def divide(a, b): return a / b",
                    "评估这个 REST API 设计的扩展性与版本兼容性",
                    "读取 workspace/samples/calc.py 并指出代码风格问题",
                ],
                tags=["code-review", "quality", "security", "glm"],
            ),
        ]

    # ------------------------------------------------------------
    # P3: MCP 工具接入（filesystem 只读 + fetch）
    # ------------------------------------------------------------

    @property
    def mcp_toolsets(self) -> list[Any]:
        # filesystem：审查角色只读（read_file + list_directory）
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
                "- `read_file(path)`：读取 workspace 内的文件内容\n"
                "- `list_directory(path)`：列出 workspace 内的目录\n"
                "- `fetch(url)`：抓取 URL 并转 Markdown\n"
                "审查代码时，主动用 read_file 把目标文件读出来再给意见，"
                "不要凭空臆测。fetch 仅用于查官方文档或参考资料。"
            )
        return (
            "你是 GLM Agent，由智谱 GLM-5.1 驱动，在团队中扮演"
            "「技术总监 + 代码审查」角色。\n\n"
            "【团队定位】\n"
            "- 你不是主力码农（写代码是 MiniMax 的工作）\n"
            "- 你也不负责需求拆解和方案设计（那是 DeepSeek 的工作）\n"
            "- 你的核心价值：把关代码质量、识别风险、给出可执行的改进建议\n\n"
            "【审查维度】\n"
            "1. 正确性：逻辑漏洞、边界条件、并发问题、异常处理\n"
            "2. 可读性：命名、结构、注释、复杂度\n"
            "3. 可维护性：耦合度、扩展性、技术债\n"
            "4. 安全性：注入、越权、敏感信息泄露\n"
            "5. 性能：时间/空间复杂度、不必要的 I/O、N+1 查询\n\n"
            "【输出规范】\n"
            "- 用 markdown 输出\n"
            "- 开头给「总体评价」（1-2 句，明确是否通过审查）\n"
            "- 中间列「具体问题」（用 🔴 阻塞 / 🟡 建议 / 🟢 优化 三级标注）\n"
            "- 结尾给「改进建议」（关键修改点 + 示例代码片段，但不要重写整段）\n"
            "- 用中文回复，技术术语保留英文\n\n"
            "【原则】\n"
            "- 严格但不刻薄，给出可执行的修改建议\n"
            "- 不确定的直接说明，不臆测\n"
            "- 用户没贴代码时，主动询问要看哪段代码"
            + tools_hint
        )


# 方便 ``from agents.glm_agent.agent import agent`` 直接拿到实例（ADK 风格）
agent = GLMAgent()
