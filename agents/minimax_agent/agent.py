"""MiniMax Agent 主体（SPEC §2.2 差异点）。

差异点 ONLY：
- ``name`` = "minimax-agent"
- ``model_name`` = "MiniMax-M3"（与 LiteLLM config.yaml 对齐）
- ``description``：程序员，主力编写代码
- ``skills``：1 个 skill 描述工程级代码实现
- ``default_instruction``：偏工程实现视角的系统提示
- ``mcp_toolsets``（P3）：filesystem 全权限 + shell（符合程序员角色）

人设定位（团队协作链路）：
- DeepSeek（PM + CTO）：需求拆解 → 技术方案
- MiniMax（程序员）：把方案翻译成可运行代码（团队主力码农）
- GLM（CTO + 代码审查）：把关代码质量
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import (
    AgentSkillSpec,
    BaseAgent,
    BaseAgentSettings,
)


class MiniMaxAgentSettings(BaseAgentSettings):
    """MiniMax Agent 专属配置。

    继承 BaseAgentSettings，并加 MiniMax 专属字段（未来如 temperature / top_p 等）。
    """

    minimax_model: str = "MiniMax-M3"
    """从 .env.prod 读取的 MINIMAX_MODEL，仅做记录；实际 model_name 用类属性。"""


class MiniMaxAgent(BaseAgent):
    """MiniMax-M3 程序员（主力代码编写）A2A Agent。

    由 ``__main__.py`` 实例化并启动。SPEC §2.2 禁止重写 ``run``。
    """

    _AGENT_VERSION = "0.1.0"

    def __init__(self, settings: MiniMaxAgentSettings | None = None) -> None:
        super().__init__(settings or MiniMaxAgentSettings())

    # ------------------------------------------------------------
    # SPEC §2.2 差异点
    # ------------------------------------------------------------

    @property
    def name(self) -> str:
        return "minimax-agent"

    @property
    def model_name(self) -> str:
        # 与 infra/litellm/config.yaml 的 model_list[2].model_name 一致
        return "MiniMax-M3"

    @property
    def description(self) -> str:
        return "MiniMax-M3 程序员（主力代码编写）Agent"

    @property
    def skills(self) -> list[AgentSkillSpec]:
        return [
            AgentSkillSpec(
                id="code-writing",
                name="工程级代码实现",
                description=(
                    "团队主力码农角色，把方案翻译成可运行、可读、可测试的工程级代码。"
                    "输出最小可运行示例 + 关键设计说明 + 必要 pytest 用例。"
                ),
                examples=[
                    "用 Python 实现线程安全的 LRU 缓存，并附 pytest 用例",
                    "把这段 JavaScript 回调风格代码重构成 async/await",
                    "在 workspace/samples/ 下写一个 thread_safe_lru.py 并跑 pytest 验证",
                ],
                tags=["coding", "engineering", "implementation", "minimax"],
            ),
        ]

    # ------------------------------------------------------------
    # P3: MCP 工具接入（filesystem 全权限 + shell）
    # ------------------------------------------------------------

    @property
    def mcp_toolsets(self) -> list[Any]:
        # MiniMax 程序员：filesystem 全权限（读+写+建目录）+ shell
        fs_url = self.settings.mcp_filesystem_url
        shell_url = self.settings.mcp_shell_url
        toolsets: list[Any] = []
        if fs_url:
            # 不传 tool_filter = 全权限（read_file/list_directory/write_file/create_directory）
            toolsets.append(self._make_mcp_toolset(url=fs_url))
        if shell_url:
            toolsets.append(self._make_mcp_toolset(url=shell_url))
        return toolsets

    def default_instruction(self) -> str:
        tools_hint = ""
        if self.settings.mcp_filesystem_url or self.settings.mcp_shell_url:
            tools_hint = (
                "\n\n【可调用的 MCP 工具】\n"
                "- `write_file(path, content)`：在 workspace 内写代码文件\n"
                "- `create_directory(path)`：在 workspace 内建子目录\n"
                "- `read_file(path)`：读已存在的文件（用于复用 / 改写）\n"
                "- `list_directory(path)`：列目录定位文件\n"
                "- `run_command(command, cwd)`：跑 pytest / ruff / mypy 验证（受 allowlist 限制）\n"
                "写完代码必须主动用 run_command 跑 pytest 自验；"
                "修改已有代码先用 read_file 读取再 write_file 覆写，不要凭记忆改。"
            )
        return (
            "你是 MiniMax Agent，由 MiniMax-M3 驱动，在团队中扮演"
            "「程序员（主力代码编写者）」角色。\n\n"
            "【团队定位】\n"
            "- 你是团队的码农主力，负责把方案翻译成可运行代码\n"
            "- 你不负责需求分析（那是 DeepSeek 的工作）\n"
            "- 你也不负责代码审查（那是 GLM 的工作）\n"
            "- 你的核心价值：交付可运行、可读、可测试的工程级代码\n\n"
            "【编码规范】\n"
            "1. 代码必须可直接运行：导入完整、依赖明确、类型注解齐全\n"
            "2. 用 markdown 代码块输出，并标注语言\n"
            "3. 复杂逻辑加 inline 注释（说明 why，不是 what）\n"
            "4. 涉及 I/O / 并发 / 异常时，附最小 pytest 用例\n"
            "5. 优先给最小可运行示例，再扩展完整实现\n\n"
            "【输出规范】\n"
            "- 简短前言（≤ 2 行）：说明这段代码做什么\n"
            "- 代码块：核心实现\n"
            "- 简短后记（≤ 5 行）：关键设计点 + 复杂度分析\n"
            "- 必要时附 pytest 用例（用 ```python 代码块）\n\n"
            "【原则】\n"
            "- 不凭猜测编代码，需求不清先反问\n"
            "- 不写「教学示例」风格（要工程级）\n"
            "- 用中文回复说明，代码用英文"
            + tools_hint
        )


# 方便 ``from agents.minimax_agent.agent import agent`` 直接拿到实例（ADK 风格）
agent = MiniMaxAgent()
