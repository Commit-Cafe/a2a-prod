"""Agent 共享基类（SPEC §2.1）。

所有具体 Agent（GLM/DeepSeek/MiniMax）通过继承 ``BaseAgent`` 实现差异化。
具体 Agent **只允许**覆盖 ``name`` / ``model_name`` / ``description`` / ``skills``，
禁止重写 ``run``（SPEC §2.2）。

实现思路（与 ADK 1.34 + a2a-sdk 0.3.x 对齐，ADR-0005）：

1. ``build_agent()`` 构建 ADK ``LlmAgent``，model 走 LiteLLM（OpenAI 兼容）
2. ``build_card()`` 构建 a2a-sdk ``AgentCard``
3. ``build_executor()`` 构建 ``AgentExecutor``：用 ADK ``Runner`` 驱动 ``LlmAgent``
4. ``run()`` 用 a2a-sdk ``A2AStarletteApplication`` + uvicorn 暴露 A2A 服务
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from typing import Any

import structlog
import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    InternalError,
    TaskState,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import new_agent_text_message
from a2a.utils.errors import ServerError
from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.genai import types as genai_types
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger(__name__)


# ============================================================
# 配置
# ============================================================


class BaseAgentSettings(BaseSettings):
    """BaseAgent 共享配置。具体 Agent 可继承并扩展。

    配置项全部走 ``pydantic-settings``（SPEC §2.4）。
    """

    model_config = SettingsConfigDict(
        env_file=".env.prod",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # LiteLLM 入口
    litellm_base_url: str = Field(
        default="http://litellm:4000/v1",
        description="LiteLLM Proxy 入口 URL（容器内通过 service name）",
    )
    litellm_master_key: str = Field(
        default="",
        description="LiteLLM master key，用于 Authorization: Bearer 头",
    )

    # 服务
    host: str = Field(default="0.0.0.0", description="HTTP 监听地址")
    port: int = Field(default=8000, description="HTTP 监听端口（容器内）")

    # 日志
    log_level: str = Field(default="INFO", description="structlog 日志级别")
    structlog_devmode: bool = Field(default=False, description="structlog 彩色输出")

    # P3: MCP server URL（容器内通过 service name）
    mcp_filesystem_url: str | None = Field(
        default=None,
        description="Filesystem MCP server URL，如 http://mcp-filesystem:12101/mcp",
    )
    mcp_fetch_url: str | None = Field(
        default=None,
        description="Fetch MCP server URL，如 http://mcp-fetch:12102/mcp",
    )
    mcp_shell_url: str | None = Field(
        default=None,
        description="Shell MCP server URL，如 http://mcp-shell:12103/mcp",
    )


class AgentSkillSpec(BaseModel):
    """单个 skill 的声明式描述（避免子类直接构造 a2a-sdk 对象）。"""

    id: str
    name: str
    description: str
    examples: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


# ============================================================
# 异常（SPEC §2.6 业务层）
# ============================================================


class AgentError(Exception):
    """Agent 业务层基类异常。"""


class LLMTimeoutError(AgentError):
    """LLM 调用超时。"""


class LLMUnauthorizedError(AgentError):
    """LLM API Key 无效或额度耗尽。"""


class LiteLLMRoutingError(AgentError):
    """LiteLLM 路由失败（model 名不存在等）。"""


# ============================================================
# BaseAgent 抽象
# ============================================================


class BaseAgent(ABC):
    """三 Agent 共享基类。

    子类 MUST 覆盖：``name`` / ``model_name`` / ``description`` / ``skills``。
    子类 SHOULD NOT 覆盖：``build_agent`` / ``build_executor`` / ``run``。
    """

    # 子类必须显式声明（让 mypy strict 通过）
    _AGENT_VERSION: str = "0.1.0"

    def __init__(self, settings: BaseAgentSettings | None = None) -> None:
        self.settings: BaseAgentSettings = settings or BaseAgentSettings()
        # 配置 structlog（SPEC §2.5）
        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(structlog, self.settings.log_level.lower(), 20)
            ),
        )

    # ------------------------------------------------------------
    # 子类必须实现的属性（SPEC §2.1）
    # ------------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Agent 唯一名（kebab-case），如 'glm-agent'。用于 Agent Card。"""

    @property
    def agent_id(self) -> str:
        """ADK 内部用的 Python identifier（不能有 '-'）。

        默认实现：把 name 的 '-' 换成 '_'，如 'glm-agent' → 'glm_agent'。
        子类可以覆盖。
        """
        return self.name.replace("-", "_")

    @property
    @abstractmethod
    def model_name(self) -> str:
        """对应 LiteLLM config.yaml 中的 model_name，如 'glm-5.1'。"""

    @property
    @abstractmethod
    def description(self) -> str:
        """一句话描述 Agent 能力（写入 Agent Card）。"""

    @property
    @abstractmethod
    def skills(self) -> list[AgentSkillSpec]:
        """至少 1 个 skill（SPEC §1.1）。"""

    # ------------------------------------------------------------
    # P3: MCP 工具接入（子类可覆盖 mcp_toolsets）
    # ------------------------------------------------------------

    @property
    def mcp_toolsets(self) -> list[Any]:
        """子类可覆盖：返回要注入 LlmAgent.tools 的 MCPToolset 实例列表。

        默认空列表（不接入 MCP）。子类 SHOULD 用 ``_make_mcp_toolset`` 构造。
        """
        return []

    def _make_mcp_toolset(
        self,
        url: str,
        tool_filter: list[str] | None = None,
    ) -> Any:
        """构造 ADK McpToolset（Streamable HTTP）。

        Args:
            url: MCP server URL，如 ``http://mcp-filesystem:12101/mcp``
            tool_filter: 仅暴露这些 tool 名；None 表示暴露全部
        """
        from google.adk.tools.mcp_tool import (  # noqa: PLC0415
            McpToolset,
            StreamableHTTPConnectionParams,
        )

        return McpToolset(
            connection_params=StreamableHTTPConnectionParams(url=url),
            tool_filter=tool_filter,
        )

    # ------------------------------------------------------------
    # 默认实现（SPEC §2.2 禁止子类重写）
    # ------------------------------------------------------------

    def _build_litellm_model(self) -> str:
        """构造 LiteLLM 调用所需的 model 字符串。

        走 ``openai/<model_name>`` 路由（SPEC §2.3），由 LiteLLM 转发到真实 API。
        """
        return f"openai/{self.model_name}"

    def _build_litellm_kwargs(self) -> dict[str, Any]:
        """LiteLlm 构造参数（注入 base_url 与 api_key）。"""
        kwargs: dict[str, Any] = {
            "model": self._build_litellm_model(),
            "api_base": self.settings.litellm_base_url,
        }
        if self.settings.litellm_master_key:
            kwargs["api_key"] = self.settings.litellm_master_key
        return kwargs

    def build_agent(self) -> LlmAgent:
        """构造 ADK LlmAgent。

        - name: agent_id（Python identifier，ADK 要求）
        - model: LiteLlm(openai/<model_name>)，走 LiteLLM Proxy
        - instruction: 默认占位，子类 SHOULD 覆盖 ``default_instruction`` 提供更具体提示
        - tools: P3 起注入子类声明的 MCPToolset（来自 ``mcp_toolsets`` 属性）
        """
        return LlmAgent(
            name=self.agent_id,
            model=LiteLlm(**self._build_litellm_kwargs()),
            description=self.description,
            instruction=self.default_instruction(),
            tools=self.mcp_toolsets,
        )

    def default_instruction(self) -> str:
        """LLM 系统提示。子类 SHOULD 覆盖。"""
        return (
            f"You are {self.name}, a helpful assistant powered by {self.model_name}. "
            "Respond concisely and accurately in the user's language."
        )

    def build_card(self, *, public_url: str | None = None) -> AgentCard:
        """构造 a2a-sdk AgentCard（SPEC §1.1）。

        Args:
            public_url: Agent 对外可达 URL；不传时用 ``f"http://localhost:{port}/"``。
        """
        url = public_url or f"http://localhost:{self.settings.port}/"
        return AgentCard(
            name=self.name,
            description=self.description,
            version=self._AGENT_VERSION,
            url=url,
            capabilities=AgentCapabilities(streaming=True, push_notifications=False),
            skills=[
                AgentSkill(
                    id=s.id,
                    name=s.name,
                    description=s.description,
                    examples=s.examples,
                    tags=s.tags,
                )
                for s in self.skills
            ],
            # 与 .env.example 中的 protocol_version 字段对齐（SPEC §1.1 SHOULD）
            # a2a-sdk 0.3.x 字段全部 snake_case（pydantic 模型自动序列化为 camelCase JSON）
            protocol_version="0.3.0",
            default_input_modes=["text"],
            default_output_modes=["text"],
        )

    # ------------------------------------------------------------
    # AgentExecutor（SPEC §1.3：必须实现 message/send + message/stream）
    # ------------------------------------------------------------

    def build_executor(self) -> AgentExecutor:
        """构造 a2a-sdk AgentExecutor。

        默认实现：
        1. 从 RequestContext 解析用户文本
        2. 用 ADK Runner 驱动 LlmAgent
        3. 把 ADK Events 转 a2a TaskUpdater 事件
        """
        from google.adk.sessions.in_memory_session_service import (
            InMemorySessionService,
        )

        agent = self.build_agent()
        runner = Runner(
            app_name=self.agent_id,
            agent=agent,
            # P0+P1 不持久化 session，用 InMemorySessionService 即可
            session_service=InMemorySessionService(),  # type: ignore[no-untyped-call]
        )
        return _BaseAgentExecutor(self, runner)

    # ------------------------------------------------------------
    # 启动入口
    # ------------------------------------------------------------

    async def run(self, *, host: str | None = None, port: int | None = None) -> None:
        """启动 A2A Server（uvicorn + A2AStarletteApplication）。

        子类 SHOULD NOT 重写（SPEC §2.2）。
        """
        host = host or self.settings.host
        port = port or self.settings.port

        card = self.build_card(public_url=f"http://{host}:{port}/")
        executor = self.build_executor()
        request_handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=InMemoryTaskStore(),
        )
        server = A2AStarletteApplication(
            agent_card=card,
            http_handler=request_handler,
        )

        logger.info(
            "agent_starting",
            agent=self.name,
            model=self.model_name,
            host=host,
            port=port,
            version=self._AGENT_VERSION,
        )

        # uvicorn.run 是同步阻塞；在 async 函数里用 Server.run 走 event loop
        config = uvicorn.Config(server.build(), host=host, port=port, log_level="info")
        uvicorn_server = uvicorn.Server(config)
        await uvicorn_server.serve()


# ============================================================
# 默认 AgentExecutor 实现
# ============================================================


class _BaseAgentExecutor(AgentExecutor):
    """BaseAgent 默认 Executor。

    把 a2a RequestContext → ADK Runner → a2a TaskUpdater 串起来。
    """

    def __init__(self, base: BaseAgent, runner: Runner) -> None:
        self._base = base
        self._runner = runner
        self._log = structlog.get_logger(f"agent.executor.{base.name}")

    # ------------------------------------------------------------
    # message/send 与 message/stream 共用入口
    # ------------------------------------------------------------

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """处理一次 message 请求（同步 / 流式共用）。"""
        # a2a-sdk 0.3：TaskUpdater 需要 task_id + context_id（由 RequestContext 提供）
        # context.task_id 可能为 None（首次请求），由 SDK 自动生成
        task_id = context.task_id or f"task-{os.urandom(4).hex()}"
        context_id = context.context_id or f"ctx-{os.urandom(4).hex()}"
        task_updater = TaskUpdater(event_queue, task_id=task_id, context_id=context_id)

        # 1. 提取用户文本
        user_text = self._extract_user_text(context)
        if not user_text:
            await task_updater.reject(message=new_agent_text_message("[reject] empty user message"))
            return

        # 2. ADK Runner 跑 LlmAgent
        await task_updater.start_work()
        try:
            final_text = await self._run_adk_agent(user_text, context, task_updater)
        except TimeoutError as e:
            self._log.warning("llm_timeout", model=self._base.model_name)
            raise ServerError(InternalError(message="llm_timeout")) from e
        except ServerError:
            raise
        except Exception as e:
            self._log.exception("llm_call_failed", error=str(e))
            raise ServerError(InternalError(message=f"llm_failed: {e!r}")) from e

        # 3. 终态：completed + 最终消息（new_agent_text_message 自动构造 role=agent 的 Message）
        await task_updater.complete(message=new_agent_text_message(final_text))

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """P0+P1 不实现 cancel（SPEC §1.3 MAY）。"""
        raise ServerError(UnsupportedOperationError(message="cancel not supported"))

    # ------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------

    @staticmethod
    def _extract_user_text(context: RequestContext) -> str:
        """从 A2A RequestContext 中拼出用户文本。"""
        if context.message is None or context.message.parts is None:
            return ""
        chunks: list[str] = []
        for part in context.message.parts:
            # a2a-sdk 0.3: Part 是 union，root 才是具体类型
            root = getattr(part, "root", part)
            if isinstance(root, TextPart):
                chunks.append(root.text)
        return "\n".join(chunks).strip()

    async def _run_adk_agent(
        self,
        user_text: str,
        context: RequestContext,
        task_updater: TaskUpdater,
    ) -> str:
        """驱动 ADK Runner，返回最终文本。

        - session_id 用 A2A 的 context_id（若为空则新开）
        - 流式中间块通过 task_updater.update_status("working") 转发
        """
        # ADK Runner 需要 session；用 context_id 作为 session_id 保持多轮
        session_service = self._runner.session_service
        user_id = "a2a-user"  # P0+P1 不区分用户
        session_id = context.context_id or f"a2a-session-{os.urandom(4).hex()}"

        session = await session_service.create_session(
            app_name=self._runner.app_name,
            user_id=user_id,
            session_id=session_id,
        )

        content = genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=user_text)],
        )

        final_text_parts: list[str] = []
        async for event in self._runner.run_async(
            user_id=user_id,
            session_id=session.id,
            new_message=content,
        ):
            # ADK event 是 Event(dataclass)，含 content 与 partial / final 标志
            text = self._extract_adk_event_text(event)
            if not text:
                continue
            if getattr(event, "partial", False):
                # 流式中间块：发 working 状态（SPEC §1.4 中间事件）
                # TaskState 是 str 枚举，成员名小写（TaskState.working）
                await task_updater.update_status(
                    state=TaskState.working,
                    message=new_agent_text_message(text),
                )
            else:
                final_text_parts.append(text)

        return "\n".join(final_text_parts).strip()

    @staticmethod
    def _extract_adk_event_text(event: Any) -> str:
        """从 ADK Event 中提取文本片段。"""
        content = getattr(event, "content", None)
        if content is None:
            return ""
        parts = getattr(content, "parts", None) or []
        chunks: list[str] = []
        for p in parts:
            text = getattr(p, "text", None)
            if isinstance(text, str) and text:
                chunks.append(text)
        return "".join(chunks)


# ============================================================
# CLI 启动工具（具体 Agent 的 __main__.py 复用）
# ============================================================


def run_agent_cli(agent: BaseAgent) -> None:
    """具体 Agent 的 ``__main__.py`` 调用此函数启动。

    用法::

        if __name__ == "__main__":
            run_agent_cli(MyAgent())
    """
    # P4: 在 A2A server 启动前注入 trace（失败不阻塞 Agent 启动）
    try:
        from observability.setup import setup_agent

        setup_agent()
    except Exception as e:  # noqa: BLE001 - 启动期不能崩
        import structlog as _sl

        _sl.get_logger(__name__).warning("setup_agent_failed_continue", error=str(e))

    # asyncio.run 包装 BaseAgent.run
    asyncio.run(agent.run())
