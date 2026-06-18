# SPEC — a2a-prod 项目规范

> **当前版本**：v0.8.0（对应 P6 引入 K8s 部署清单）
> **下一计划版本**：v1.0.0（生产化 / BREAKING CHANGE 候选）
>
> 本文档是 a2a-prod 的**项目规范单一事实源**（Single Source of Truth）。
> 任何代码改动 MUST 与本文档一致；文档与代码冲突时，**以本文档为准**，
> 除非先通过 ADR 修改本文档（见 §4.5）。
>
> 本文档与项目其它文档的关系见 §0.1。

---

## 0. 总则

### 0.1 文档定位

| 文档 | 作用 | 与 SPEC.md 的关系 |
|---|---|---|
| `NORTH_STAR.md` | 项目愿景 / 做 vs 不做 | SPEC 的上位原则；SPEC 不得违反 NORTH_STAR |
| `ARCHITECTURE.md` | 架构图、组件清单、阶段路线 | SPEC 描述"应该是什么"，ARCHITECTURE 描述"如何拆阶段实现" |
| `CODESTYLE.md` | 代码风格细节（缩进、引号、行长） | SPEC §2 / §4 引用 CODESTYLE，不重复 |
| `DECISIONS.md` | ADR 架构决策记录 | 修改 SPEC MUST 留 ADR |
| `README.md` | 启动指南、用户视角 | README MUST 与 SPEC 一致 |

冲突仲裁顺序（高 → 低）：
**NORTH_STAR → SPEC.md → DECISIONS.md → ARCHITECTURE.md → CODESTYLE.md → README.md**。

### 0.2 关键字约定（RFC 2119）

| 关键字 | 含义 |
|---|---|
| **MUST** / **MUST NOT** | 绝对要求；违反即视为缺陷 |
| **SHOULD** / **SHOULD NOT** | 强烈推荐；偏离需在 PR 描述或 ADR 中说明理由 |
| **MAY** | 可选项；不做强制 |

### 0.3 spec 与代码同步

- 任何 PR MUST 在描述里声明："本 PR 不影响 spec"或"本 PR 同步修改 spec §X.Y"。
- 代码与 spec 不一致的 issue 标签为 `spec-violation`，**阻塞合并**。
- 修改 spec 的 PR MUST 在 §5 变更日志记录版本号变化。

---

## 1. A2A 协议层 spec

> 本章只规定 a2a-prod **实际用到**的 A2A v0.3 协议子集。
> 完整规范见 https://a2a-protocol.org/ 。
> 未提到的能力（pushNotifications、file part、data part 等）**不在 P0+P1 范围**。

### 1.1 Agent Card 强制字段

每个 Agent MUST 暴露 `GET /.well-known/agent.json`，返回 JSON 必须包含以下字段：

| 字段 | 类型 | MUST / SHOULD | 说明 |
|---|---|---|---|
| `name` | string | MUST | 全局唯一，建议 kebab-case：`glm-agent` / `deepseek-agent` / `minimax-agent` |
| `description` | string | MUST | 一句话说明 Agent 能力 |
| `version` | string | MUST | SemVer，与镜像 tag 一致（见 §3.5） |
| `capabilities.streaming` | bool | MUST | a2a-prod 内 Agent MUST 设为 `true` |
| `capabilities.pushNotifications` | bool | MUST | P0+P1 MUST 设为 `false` |
| `skills` | array | MUST | 至少 1 个 skill；每个 skill MUST 有 `id` / `name` / `description` |
| `skills[].examples` | array | SHOULD | 给调用方提示用法 |
| `url` | string | MUST | Agent 自身可达 URL（如 `http://glm-agent:8000/`） |
| `protocolVersion` | string | SHOULD | 值为 `0.3.0`，与 a2a-sdk 锁定版本对齐 |

**禁止**字段：`authentication.schemes`（P0+P1 不鉴权，P6 阶段再补）。

**守护点**：本节契约由 `tests/contract/test_agent_card.py` 守护（P0-5 阶段补）。

### 1.2 Agent Card 暴露路径

- HTTP 路径 MUST 为 `/.well-known/agent.json`（注意末尾无 `/`）。
- HTTP 方法 MUST 仅支持 `GET`，其它方法返回 405。
- 响应 `Content-Type` MUST 为 `application/json; charset=utf-8`。
- 响应 MUST 不依赖任何鉴权头。

### 1.3 必须实现的 A2A 方法

每个 Agent MUST 实现以下 JSON-RPC 方法（A2A 标准 method 名）：

| 方法 | 用途 | 同步 / 流式 | P0+P1 必须 |
|---|---|---|---|
| `message/send` | 单次请求 / 单次响应 | 同步 | MUST |
| `message/stream` | 单次请求 / SSE 流式响应 | 流式 | MUST |
| `tasks/get` | 查询任务状态 | 同步 | SHOULD（P0+P1 可返回 `task not found`） |
| `tasks/cancel` | 取消任务 | 同步 | MAY（P0+P1 可不实现） |
| `tasks/resubscribe` | 重新订阅流 | 流式 | MAY |

**守护点**：本节由 `tests/contract/test_a2a_methods.py` 守护。

### 1.4 SSE 事件序列契约

`message/stream` 响应 MUST 按以下顺序发送事件：

```
1. event: task/update        data: {"state": "submitted"}
2. event: task/update        data: {"state": "working"}     ← 可多次
3. event: task/update        data: {"state": "working", "message": {...}}  ← 流式块（≥0 次）
4. event: task/completed     data: {"state": "completed", "message": {...}}
                                或
   event: task/failed        data: {"state": "failed", "message": {...}}
```

约束：
- 第一个事件 MUST 是 `state=submitted`（任务已接收）。
- 中间事件 MUST 是 `state=working`。
- 终态事件 MUST 是 `completed` 或 `failed`，且 MUST NOT 之后再有事件。
- 终态事件 MUST 包含最终 `message` 字段（即模型完整输出）。

**守护点**：本节由 `tests/e2e/test_stream_sequence.py` 守护。

### 1.5 错误码映射

| 上游错误 | 映射到 A2A JSON-RPC 错误码 | HTTP 状态（兼容 REST） |
|---|---|---|
| LLM API 超时 | `-32603` internal error（message: `llm_timeout`） | 500 |
| LLM API Key 无效 / 余额不足 | `-32001` unauthorized | 401 |
| LiteLLM 路由失败（model 名不存在） | `-32602` invalid params | 400 |
| 客户端请求格式不合规 | `-32700` parse error | 400 |
| 客户端用了不支持的方法 | `-32601` method not found | 404 |

错误响应 MUST 包含 `code` / `message` / `data.request_id` 三字段。

### 1.6 流式响应的 timeout 与重试

- 单次 LLM 调用 timeout MUST = 60s（与 `infra/litellm/config.yaml` 一致）。
- LiteLLM 内部 retry MUST = 3（指数退避）。
- Agent 自身不再做 LLM 重试（避免重试叠加）。
- SSE 心跳：SHOULD 每 15s 发一个 `:keep-alive` 注释行（防止反向代理断连）。

---

## 2. Agent 接口 spec

### 2.1 BaseAgent 抽象

`agents/base_agent.py` MUST 定义 `BaseAgent` 抽象类，具体 Agent（GLM / DeepSeek / MiniMax）MUST 继承它。

`BaseAgent` MUST 提供以下抽象方法 / 属性：

```python
class BaseAgent(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Agent 唯一名（kebab-case），如 'glm-agent'。"""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """对应 LiteLLM config.yaml 中的 model_name，如 'glm-5.1'。"""

    @abstractmethod
    def build_card(self) -> AgentCard: ...
    @abstractmethod
    def build_executor(self) -> AgentExecutor: ...
    @abstractmethod
    async def run(self, *, host: str = "0.0.0.0", port: int = 8000) -> None: ...
```

具体 Agent SHOULD 只覆盖 `name` / `model_name` / Agent Card 描述，**禁止**重写 `run`。

### 2.2 具体 Agent 的差异点

GLM / DeepSeek / MiniMax 三个 Agent 的差异**仅允许**在以下维度：

| 维度 | GLM | DeepSeek | MiniMax |
|---|---|---|---|
| `name` | `glm-agent` | `deepseek-agent` | `minimax-agent` |
| `model_name` | `glm-5.1` | `deepseek-chat` | `MiniMax-M3` |
| `description` | 中文理解 / 内容生成 | 代码 / 推理 | 长上下文 / 多语言 |
| `skills` | 各自 ≥ 1 个 skill，描述差异化 | 同左 | 同左 |

**禁止**：
- 具体 Agent 重写 `run` 或绕过 BaseAgent 直接启动 ADK。
- 具体 Agent 自行 import `openai` / `httpx`（统一走 ADK LlmAgent → LiteLLM）。

### 2.3 与 LiteLLM 的调用契约

- Agent 调用 LLM MUST 通过 ADK 的 `LlmAgent`，model 参数 MUST 形如 `openai/<model_name>`（让 LiteLLM 走 OpenAI 兼容路由）。
- LiteLLM 入口 URL 通过环境变量 `LITELLM_BASE_URL` 注入，默认 `http://litellm:4000/v1`。
- LiteLLM master key 通过 `LITELLM_MASTER_KEY` 注入，请求头 `Authorization: Bearer ${LITELLM_MASTER_KEY}`。
- Agent MUST NOT 直连 GLM / DeepSeek / MiniMax 官方 API（绕过 LiteLLM 视为 spec 违反）。

### 2.4 配置注入

- 所有可配置项 MUST 走 `pydantic-settings`，定义在 `agents/<agent>/settings.py` 或 `host/settings.py`。
- 环境变量 MUST 从 `.env.prod` 加载（开发可走 `.env`）。
- **禁止**硬编码：API Key、端口、模型名、URL、超时。
- **禁止**用 `os.getenv` 直接读，MUST 走 pydantic Settings 实例。

### 2.5 日志

- 日志 MUST 走 `structlog`。
- 每条日志 MUST 包含 `request_id` 字段（由 FastAPI / Starlette 中间件注入）。
- 日志级别：`INFO` 默认；调试可 `DEBUG`；不输出 `api_key` / `Authorization` 字段。
- **禁止**用 `print()` 输出业务信息（容器内 stdout 由 docker 收集）。
- **禁止**用 f-string 拼接日志，MUST 用结构化字段：`logger.info("llm_call", model=..., latency=...)`。

### 2.6 异常分层

```
基础设施层（httpx / openai / a2a-sdk 抛原生异常）
        │
        ▼
业务层（agents/<agent>/exceptions.py 定义领域异常）
        │   - LLMTimeout
        │   - LLMUnauthorized
        │   - LiteLLMRoutingError
        ▼
协议层（host/ 或 agent 内的 handler 转 A2A JSON-RPC 错误码）
```

- 业务层 MUST NOT 抛原生 `Exception`，MUST 抛领域异常。
- 协议层 MUST 全捕获并按 §1.5 映射成 A2A 错误码。
- **禁止**裸 `except:` / 静默 `except Exception: pass`（CODESTYLE §5.2 也已规定）。

---

## 3. 基础设施 spec

### 3.1 端口分配

| 服务 | 端口 | 阶段 | 来源 |
|---|---|---|---|
| LiteLLM Proxy | 4000 | P0 | `LITELLM_PROXY_PORT` |
| GLM Agent | 12001 | P0 | `GLM_AGENT_PORT` |
| DeepSeek Agent | 12002 | P1 | `DEEPSEEK_AGENT_PORT` |
| MiniMax Agent | 12003 | P1 | `MINIMAX_AGENT_PORT` |
| Orchestrator | 12080 | P2 | `ORCHESTRATOR_PORT` |
| Filesystem MCP | 12101 | P3 | `MCP_FILESYSTEM_PORT` |
| Fetch MCP | 12102 | P3 | `MCP_FETCH_PORT` |
| Shell MCP | 12103 | P3 | `MCP_SHELL_PORT` |
| Langfuse | 3000 | P4 | `LANGFUSE_PORT` |
| Open WebUI | 8080 | P5 | `OPEN_WEBUI_PORT` |

约束：
- **MUST NOT** 使用 11000-11099 段（属于原 a2a-agents）。
- **MUST NOT** 在代码里硬编码端口，MUST 走环境变量 + 默认值。

### 3.2 docker-compose 命名规则

- project name MUST = `a2a-prod`（compose 文件顶部 `name:` 字段）。
- network MUST = `a2a-prod-net`（driver: bridge）。
- 数据卷 MUST = `a2a-prod-data`。
- 容器名 MUST 形如 `a2a-prod-<service>`，例如 `a2a-prod-glm-agent`。
- 服务名 MUST 用 kebab-case，与 docker-compose services key 一致。

### 3.3 Dockerfile 强制要求

所有 Agent 容器共享的 `agents/Dockerfile`：

- 基镜像 MUST = `python:3.12-slim`（与 `.python-version` 一致）。
- MUST 创建非 root 用户（如 `a2a`），`USER a2a` 在 `CMD` 之前。
- MUST 设置 `PYTHONUNBUFFERED=1` / `PYTHONDONTWRITEBYTECODE=1`。
- MUST 包含 `HEALTHCHECK`，引用 `scripts/healthcheck.py`。
- MUST 配套 `.dockerignore`，至少排除 `.venv-prod/` / `.git/` / `build/` / `dist/` / `*.md`（保留 `README.md`）。
- SHOULD 用 `uv pip install --system`（比 pip 快 5-10 倍）。

LiteLLM 用官方镜像 `ghcr.io/berriai/litellm:main-stable`，**禁止**自建 LiteLLM 镜像。

### 3.4 LiteLLM config.yaml 命名

`infra/litellm/config.yaml` 的 `model_list` 命名规则：

- `model_name` MUST 与 `.env.example` 中的 `*_MODEL` 一致：
  - `glm-5.1` ← `GLM_MODEL`
  - `deepseek-chat` ← `DEEPSEEK_MODEL`
  - `MiniMax-M3` ← `MINIMAX_MODEL`
- `litellm_params.model` MUST 加 `openai/` 前缀（走 OpenAI 兼容路由）。
- API Key / Base URL MUST 用 `os.environ/<VAR>` 引用，**禁止**写明文。

### 3.5 镜像 tag 规则

- Agent 镜像 tag MUST = `a2a-prod-<agent>:<semver>`，例如 `a2a-prod-glm-agent:0.1.0`。
- 版本号与 Agent Card 的 `version` 字段 MUST 一致。
- latest tag MUST NOT 使用（避免隐式升级）。

### 3.6 启动顺序与依赖

`docker-compose up` 时：

1. LiteLLM MUST 先启动并通过 healthcheck（`/health/liveness` 返回 2xx）。
2. 三个 Agent MUST `depends_on: { litellm: { condition: service_healthy } }`。
3. Agent 启动后 SHOULD 自检：拉一次自家 Agent Card，失败则容器退出非 0。
4. P3 起，三个 MCP server MUST 先于 Agent 启动并通过 `:/healthz` healthcheck。
5. Agent MUST `depends_on: { mcp-*: { condition: service_healthy } }`（仅依赖它实际接入的 MCP server）。

### 3.7 MCP 工具生态（P3 起）

> P3 阶段引入 MCP (Model Context Protocol) 让 Agent 调用外部工具。详见 ADR-0007。

#### 3.7.1 Server 清单与端口

| Server | 端口 | 来源 | 暴露路径 |
|---|---|---|---|
| Filesystem MCP | `MCP_FILESYSTEM_PORT`（默认 12101） | 自研（FastMCP） | `/mcp` |
| Fetch MCP | `MCP_FETCH_PORT`（默认 12102） | 复用官方 `mcp-server-fetch` + wrapper | `/mcp` |
| Shell MCP | `MCP_SHELL_PORT`（默认 12103） | 自研（FastMCP） | `/mcp` |

约束：
- 所有 MCP server MUST 暴露独立 `/healthz` 端点（返回 200 + JSON `{"status":"ok"}`），用于 docker healthcheck。
- MCP 协议端点 MUST 挂在 `/mcp` 路径（Streamable HTTP transport）。
- 容器名 MUST 形如 `a2a-prod-mcp-<tool>`，例如 `a2a-prod-mcp-filesystem`。
- 镜像 tag MUST = `a2a-prod-mcp-<tool>:<semver>`。

#### 3.7.2 Filesystem MCP 工具契约

Filesystem MCP server MUST 实现以下 tools：

| Tool | 参数 | 权限模型 | 说明 |
|---|---|---|---|
| `read_file` | `path: str` | 所有 Agent 可用 | 返回文本内容（>1MB 截断到前 1MB） |
| `list_directory` | `path: str` | 所有 Agent 可用 | 返回 entries 列表（name + type + size） |
| `write_file` | `path: str, content: str` | 仅 MiniMax Agent | 写文件（覆盖式） |
| `create_directory` | `path: str` | 仅 MiniMax Agent | mkdir -p 语义 |

**安全约束（MUST）：**

- 暴露根 MUST = `WORKSPACE_ROOT` 环境变量（默认 `/app/workspace`）。
- 所有 path 参数 MUST 通过 `os.path.realpath` 解析，并校验解析后路径 MUST 以 `WORKSPACE_ROOT` 为前缀（带尾部分隔符），否则返回 MCP error（`INVALID_PARAMS`，message: `path_escape`）。
- `write_file` / `create_directory` MUST 通过请求头 `X-Agent-Name` 或调用方声明的 agent 身份做 RBAC 校验；非 MiniMax 调用返回 `INVALID_PARAMS`，message: `not_allowed`。
- **禁止**暴露符号链接穿透（realpath 已覆盖）、`..` 路径、绝对路径绕过。

**守护点**：本节由 `tests/contract/test_mcp_filesystem.py` 守护（含路径逃逸 payload、越权写入 payload）。

#### 3.7.3 Fetch MCP 工具契约

Fetch MCP server MUST 暴露官方 `mcp-server-fetch` 提供的 `fetch` tool：

| Tool | 参数 | 说明 |
|---|---|---|
| `fetch` | `url: str, max_length: int = 5000, start_index: int = 0, raw: bool = False` | 抓取 URL 并返回 Markdown / 原始 HTML |

**安全约束（MUST）：**

- URL MUST 是 `http://` 或 `https://` 协议，否则返回 `INVALID_PARAMS`。
- **SHOULD** 限制访问私网地址（`127.0.0.0/8` / `10.0.0.0/8` / `172.16.0.0/12` / `192.168.0.0/16` / `169.254.0.0/16`），防止 SSRF；P3 阶段可放行（开发环境），P6 阶段强制。
- `max_length` MUST ≤ 50000（防止内存爆炸）。

**守护点**：本节由 `tests/contract/test_mcp_fetch.py` 守护（含非法 scheme payload、超长 max_length payload）。

#### 3.7.4 Shell MCP 工具契约

Shell MCP server MUST 暴露 `run_command` tool：

| Tool | 参数 | 说明 |
|---|---|---|
| `run_command` | `command: str, cwd: str = "/app/workspace"` | 在 allowlist 内执行命令，返回 stdout + stderr + exit_code |

**安全约束（MUST）：**

- **Allowlist（命令前缀白名单）**：`pytest` / `ruff check` / `mypy` / `git status` / `git diff` / `cat` / `ls`。
- 输入 `command` MUST 与 allowlist 中某项前缀匹配（按 token 比较，第一个 token 必须严格等于 allowlist 中的命令名），否则返回 `INVALID_PARAMS`，message: `not_in_allowlist`。
- 输入 `command` MUST NOT 包含以下任一字符：`&` / `;` / `|` / 反引号 / `$()` / `>` / `<`，否则返回 `INVALID_PARAMS`，message: `shell_metachar_forbidden`。
- 执行 MUST 用 `subprocess.run(..., shell=False, timeout=30, capture_output=True)`，超时返回 `INTERNAL_ERROR`，message: `timeout`。
- `cwd` MUST 通过 realpath + 前缀校验，与 Filesystem 沙箱一致（默认限制在 `WORKSPACE_ROOT`）。
- 仅 MiniMax Agent 允许调用（同 Filesystem write，通过 `X-Agent-Name` 头校验）。

**守护点**：本节由 `tests/contract/test_mcp_shell.py` 守护（含 allowlist 外命令、shell 元字符、超时、SSRF）。

#### 3.7.5 Agent 接入契约

| Agent | 接入的 MCP tools | 调用方式 |
|---|---|---|
| GLM | filesystem: `read_file` / `list_directory`；fetch: `fetch` | ADK `MCPToolset`（Streamable HTTP） |
| DeepSeek | filesystem: `read_file` / `list_directory`；fetch: `fetch` | 同上 |
| MiniMax | filesystem: 全部（含 write）；shell: `run_command` | 同上 |

约束：
- Agent MUST 通过 ADK `MCPToolset` 接入，**禁止**自行实现 MCP 客户端。
- MCP server URL MUST 走环境变量（`MCP_FILESYSTEM_URL` / `MCP_FETCH_URL` / `MCP_SHELL_URL`），**禁止**硬编码。
- Agent Card 的 `skills[].examples` SHOULD 包含至少 1 条体现工具能力的示例（如 "读取 workspace/sample.py 并审查代码风格"）。
- Agent 在调用 write_file / run_command 类高风险 tool 前，SHOULD 在 instruction 里要求 LLM 先 dry-run（先 read 验证再 write），具体由 Agent prompt 决定。

#### 3.8 Langfuse 可观测性集成（P4 起）

> P4 阶段引入 Langfuse v3 自托管 + OTEL trace，让每个 LLM call / tool call 都能在 dashboard 上看到。详见 ADR-0008。

#### 3.8.1 Langfuse 服务清单与端口

| 服务 | 容器端口 | 宿主机端口 | 阶段 | 说明 |
|---|---|---|---|---|
| Langfuse Web | 3000 | 3000 | P4 | `LANGFUSE_PORT`，Web UI + API |
| Langfuse Worker | 3030（内部）| — | P4 | 异步事件处理 |
| Postgres | 5432 | 5432 | P4 | 事务数据（OLTP） |
| Clickhouse | 8123 | 8123 | P4 | trace / observation OLAP |
| Redis | 6379 | 6379 | P4 | 队列 + 缓存 |
| MinIO | 9000 | 9000 | P4 | S3 兼容对象存储 |

约束：
- 所有 Langfuse 子服务 MUST 暴露独立 healthcheck（web 用 `GET /api/public/health`）。
- 容器名 MUST 形如 `a2a-prod-langfuse-<component>`，例如 `a2a-prod-langfuse-web`。
- Langfuse 数据卷 MUST 名为 `a2a-prod-langfuse-data`，由 `infra/docker-compose.yml` 显式声明。

#### 3.8.2 认证与凭证

- 开发环境 MUST 用 `LANGFUSE_INIT_*` 环境变量预置 Organization + Project + 默认 PK/SK（`pk-lf-local` / `sk-lf-local`）。
- 生产环境（P6）MUST 替换为 UI 手动创建的强凭证。
- PK/SK MUST 走 `.env.prod` 注入，**禁止**在代码、文档、commit message 中出现明文凭证。

#### 3.8.3 Trace 接入点契约

| 接入点 | 接入方式 | 捕获内容 |
|---|---|---|
| **3 Agent 进程** | `GoogleADKInstrumentor().instrument()` | LlmAgent 的 LLM call / tool call（含 MCP tool）|
| **LiteLLM 进程** | `litellm.callbacks = ["langfuse_otel"]` | 兜底所有经 LiteLLM 的请求（orchestrator 直调也覆盖）|
| **Orchestrator 进程** | `observability/tracing.py` 的 `@trace_node` 装饰器 | LangGraph 节点函数执行轨迹 |

#### 3.8.4 Trace 数据契约

每个 trace MUST 含以下 metadata 字段：

| 字段 | 类型 | 来源 | 说明 |
|---|---|---|---|
| `request_id` | str | A2A `message/send` params.metadata | 全链路唯一请求 ID |
| `session_id` | str | A2A `sessionId` 或 A2A taskId | 关联同一 session 的多次请求 |
| `user_id` | str | Orchestrator 注入 / Agent 启动参数 | 业务用户标识 |

约束：
- Orchestrator MUST 把 `request_id` 透传到下游 Agent（通过 A2A `params.metadata.request_id`）。
- 3 Agent 容器启动时 MUST 调 `get_client().auth_check()` 探活，失败则容器退出非 0。

#### 3.8.5 OTEL 端点配置（401 Bug 防御）

- Agent 容器内 `LANGFUSE_HOST` MUST = `http://langfuse:3000`（**不是** `localhost` 或 `127.0.0.1`）。
- 显式设 `OTEL_EXPORTER_OTLP_ENDPOINT` = `${LANGFUSE_HOST}/api/public/otel`。
- 显式设 `OTEL_EXPORTER_OTLP_HEADERS` = `Authorization=Basic ${BASE64(PK:SK)}`。
- 该 3 项 MUST 写入 `infra/docker-compose.yml` 的 `langfuse-web` / `litellm` / 3 Agent 服务的 `environment` 块。

**守护点**：本节契约由 `tests/contract/test_langfuse_client.py` 守护（mock OTEL exporter 验证端点配置正确）。

### 3.9 Open WebUI 前端集成（P5 起）

> P5 阶段引入 [Open WebUI](https://github.com/open-webui/open-webui) 作为
> 用户交互前端，通过 Orchestrator 的 OpenAI 兼容端点对接。详见 ADR-0009。

#### 3.9.1 Open WebUI 服务清单与端口

| 服务 | 镜像 | 容器端口 | 宿主机端口 | 说明 |
|---|---|---|---|---|
| Open WebUI | `ghcr.io/open-webui/open-webui:main` | 8080 | `OPEN_WEBUI_PORT`（默认 8080） | 用户前端 + 会话管理 + 知识库 |

约束：

- Open WebUI MUST 暴露 `/health` 端点（返回 200 + OK），用于 docker healthcheck。
- 数据卷 MUST 名为 `a2a-prod-open-webui-data`，由 `infra/docker-compose.yml` 显式声明。
- 容器名 MUST = `a2a-prod-open-webui`。
- Open WebUI MUST `depends_on: { orchestrator: { condition: service_healthy } }`，
  保证 Orchestrator 先就绪再起前端（避免前端冷启时探测失败）。
- Open WebUI 与 Orchestrator 通信 MUST 走 docker 网络 `a2a-prod-net`，URL 形如
  `http://orchestrator:12080/v1`（**不可**用 `localhost`）。

#### 3.9.2 Orchestrator OpenAI 兼容层契约

Orchestrator MUST 暴露以下 OpenAI 兼容端点（详见 `frontend/config/openai-compat.md`）：

| 端点 | 方法 | 鉴权 | 说明 |
|---|---|---|---|
| `/v1/models` | GET | Bearer | 列出 3 个可路由 Agent |
| `/v1/chat/completions` | POST | Bearer | 同步 chat completion |
| `/v1/chat/completions` + `stream=true` | POST | Bearer | 流式 chat completion（SSE） |

鉴权（SPEC §3.9.2.1）：

- 所有端点 MUST 校验 `Authorization: Bearer <key>` 头。
- `<key>` MUST 与 `ORCHESTRATOR_API_KEY` 或 `LITELLM_MASTER_KEY`（兜底）严格相等。
- 未设 key 时（开发模式）MUST 跳过鉴权。
- 鉴权失败 MUST 返回 401 + `WWW-Authenticate: Bearer` 头。

#### 3.9.3 model 字段语义（SPEC §3.9.3.1）

`/v1/chat/completions` 请求的 `model` 字段语义：

| 取值 | 行为 |
|---|---|
| `glm-agent` | 强制 DIRECT 模式路由到 GLM Agent |
| `deepseek-agent` | 强制 DIRECT 模式路由到 DeepSeek Agent |
| `minimax-agent` | 强制 DIRECT 模式路由到 MiniMax Agent |
| `auto`（默认） | Orchestrator 按关键词自主分类（DIRECT 单 Agent / DECOMPOSE 多 Agent）|
| 其他值 | 兜底走 `auto`（避免 Open WebUI 自定义 model 名导致 400）|

#### 3.9.4 流式协议契约

- 响应 `Content-Type` MUST = `text/event-stream`。
- 每条事件 MUST 以 `data: ` 开头（OpenAI 规范）。
- 事件格式 MUST 符合 `chat.completion.chunk` schema。
- 第一块 `delta.content` 为空（标识 assistant 角色开始）。
- 末块 `finish_reason` MUST = `stop`。
- 收尾 MUST 为 `data: [DONE]`。

#### 3.9.5 与 OpenAI 官方差异（已知 P5 限制）

| 维度 | OpenAI 官方 | a2a-prod P5 | 升级计划 |
|---|---|---|---|
| `usage` | 真实 token 计数 | 占位 0 | P5.1 |
| `tool_calls` | 支持 | 不支持 | P5.1 评估 |
| multimodal `content` | 支持 list[Part] | 仅 str | P5.1 评估 |
| `n>1`（多 choice） | 支持 | 仅 1 choice | 不计划 |
| `logprobs` | 支持 | 不支持 | 不计划 |

#### 3.9.6 错误码

| HTTP | 触发条件 | 响应体 |
|---|---|---|
| 400 | messages 为空 / 无 user role | `{"detail": "messages must contain at least one 'user' role message"}` |
| 401 | 鉴权失败 | `{"detail": "..."}` + `WWW-Authenticate: Bearer` |
| 500 | Orchestrator 内部错误 | `{"detail": "chat completion failed: <type>: <msg>"}` |

**守护点**：本节由 `tests/contract/test_openai_compat.py`（纯契约）
与 `tests/test_p5_e2e.py`（真实联调）共同守护。

### 3.10 Kubernetes 部署清单（P6 起）

> P6 阶段将 docker-compose 部署能力扩展到 Kubernetes。详见 ADR-0010。

#### 3.10.1 部署拓扑

a2a-prod K8s 部署 MUST 落在独立 namespace `a2a-prod` 内，组件拓扑与 docker-compose
保持一致（仅替换部署方式）：

| 组件 | K8s 对象 | 副本数（min-max）|
|---|---|---|
| LiteLLM | Deployment + Service + HPA | 2-8 |
| GLM Agent | Deployment + Service + HPA | 2-6 |
| DeepSeek Agent | Deployment + Service + HPA | 2-6 |
| MiniMax Agent | Deployment + Service + HPA | 2-6 |
| Orchestrator | Deployment + Service + HPA | 2-8 |
| 3 MCP Server | Deployment + Service | 1（无 HPA）|
| Langfuse 6 组件 | Deployment + Service | 各 1-2 |
| Open WebUI | Deployment + Service + Ingress | 1（无 HPA）|

约束：

- 所有 K8s 清单 MUST 放在 `infra/k8s/` 目录；Kustomize 入口 `kustomization.yaml`。
- 所有非 Namespace 资源 MUST 在 `a2a-prod` namespace（`tests/contract/test_k8s_manifests.py::test_all_resources_in_a2a_prod_namespace` 守护）。
- 真实 Secret MUST NOT 提交到 git（仅 `secrets.yaml.example` 模板），
  部署前 cp 为 `secrets.yaml` 并填入真实值（.gitignore 已加）。

#### 3.10.2 工作负载分层与副本数

详见 `infra/k8s/README.md` §1 资源分层。

#### 3.10.3 健康检查三件套

每个有 HTTP 端点的工作负载 MUST 同时配置：

- **startupProbe**：5min 启动宽限（Langfuse init 最慢）
- **livenessProbe**：30s 一次，失败重启
- **readinessProbe**：10s 一次，决定是否接流量

对**无 HTTP 端点**的工作负载（如 Langfuse worker）MUST 用 exec 探针（如 `kill -0 1`）。

守护点：`tests/contract/test_k8s_manifests.py::test_all_workloads_have_*`。

#### 3.10.4 持久化（PVC）

仅 4 个组件需要 PVC：

| 组件 | PVC 名 | 大小 | 访问模式 |
|---|---|---|---|
| Langfuse Postgres | `langfuse-postgres-data` | 10Gi | RWO |
| Langfuse ClickHouse | `langfuse-clickhouse-data` | 50Gi | RWO |
| Langfuse MinIO | `langfuse-minio-data` | 20Gi | RWO |
| Open WebUI | `open-webui-data` | 5Gi | RWO |

约束：

- 所有 PVC MUST 显式声明 `storageClassName: standard`（可在生产改）。
- LiteLLM / 3 Agent / Orchestrator / MCP server 是无状态，**禁止**加 PVC。

#### 3.10.5 安全基线

所有容器 MUST 满足：

- `runAsNonRoot: true` + `runAsUser: 1000`（容器级或 Pod 级）
- `allowPrivilegeEscalation: false`
- `readOnlyRootFilesystem: true`（可写目录走 emptyDir / PVC）
- `capabilities.drop: [ALL]`
- 所有敏感配置走 `secretKeyRef` 注入（**禁止**用 ConfigMap 存敏感信息）

镜像策略：

- 自研镜像 MUST 锁版本（`a2a-prod-xxx:0.1.0`，**禁止** `:latest`）
- 例外：Open WebUI 用 `:main`（上游策略）+ `imagePullPolicy: Always`

守护点：`tests/contract/test_k8s_manifests.py::test_all_containers_run_as_non_root` 等。

#### 3.10.6 ConfigMap 与 Secret 分离

- **ConfigMap**（无敏感信息）：LiteLLM 配置 / Langfuse web env / 共享 host
- **Secret**（敏感信息）：API Key / DB 密码 / NextAuth 密钥

`secrets.yaml.example` MUST 包含以下 key（缺失则测试 fail）：

```
LITELLM_MASTER_KEY
GLM_API_KEY / DEEPSEEK_API_KEY / MINIMAX_API_KEY
LANGFUSE_INIT_USER_PASSWORD
LANGFUSE_INIT_PROJECT_PUBLIC_KEY / LANGFUSE_INIT_PROJECT_SECRET_KEY
NEXTAUTH_SECRET
POSTGRES_PASSWORD / MINIO_ROOT_PASSWORD / REDIS_PASSWORD
```

#### 3.10.7 网络隔离（NetworkPolicy）

- `00-namespace.yaml` MUST 有 `default-deny-all` NetworkPolicy（deny Ingress + Egress）
- `70-network-policies.yaml` 按"显式 allow"原则逐条放行：
  - DNS（kube-dns） + 上游 LLM API（出公网）
  - LiteLLM 仅接 3 Agent + Orchestrator
  - 3 Agent 仅接 Orchestrator
  - 3 MCP 仅接 3 Agent
  - Open WebUI 接 Ingress Controller
  - Langfuse Web 接 3 Agent + LiteLLM + Orchestrator

#### 3.10.8 滚动升级与 PodDisruptionBudget

策略：

- **LiteLLM / 3 Agent / Orchestrator / Langfuse Web**：`RollingUpdate` + `maxSurge=1, maxUnavailable=0`
- **Postgres / ClickHouse / Redis / MinIO / Open WebUI**：`Recreate`（单实例 + PVC）
- **terminationGracePeriodSeconds** = 30-60s（OTEL span flush 完）

PDB：

- 所有 `replicas >= 2` 的 Deployment MUST 有同名 PDB（`minAvailable: 1`）
- 守护点：`tests/contract/test_k8s_manifests.py::test_pdb_covers_all_multi_replica_workloads`

#### 3.10.9 Kustomize 入口

- `kustomization.yaml` MUST 显式 `namespace: a2a-prod`
- `kustomization.yaml` MUST 引用所有 manifest 文件（`secrets.yaml.example` 除外）
- 镜像 tag 走 `images:` 字段统一管理

守护点：`tests/contract/test_k8s_manifests.py::test_kustomization_*`。

#### 3.10.10 Ingress（占位）

P6 阶段不在 K8s 清单里默认开 Ingress（避免强行绑定 hostname）。
模板（注释形式）已在 `30-orchestrator.yaml` / `60-open-webui.yaml` 给出，
部署方按需取消注释并填入真实 hostname。

启用 Ingress 后 MUST 配合：

- Ingress Controller（推荐 nginx-ingress）
- cert-manager（自动签 TLS 证书）
- DNS 记录

详见 [docs/MIGRATION.md §4](../MIGRATION.md)。

#### 3.10.11 验证清单（CI 应自动跑）

| 工具 | 命令 | 失败处理 |
|---|---|---|
| K8s 清单契约 | `pytest -m "contract" tests/contract/test_k8s_manifests.py` | 阻塞合并 |
| `kubectl --dry-run` | `kubectl apply --dry-run=client -k infra/k8s/` | 阻塞合并 |
| `kubeconform` | `kubeconform infra/k8s/*.yaml` | 阻塞合并 |
| `kubescape` | `kubescape scan --submitter opa` | 阻塞合并 |
| k3d 真实部署 | CI 用 k3d 跑完整 e2e | 阻塞合并 |

P6 阶段 K8s 清单的 CI 验证项目 MUST 至少包含前 2 项（契约 + dry-run）。

---

## 4. 开发流程 spec

### 4.1 Git 分支模型

- `main`：受保护分支，PR 才能合并；MUST 通过 §4.3 质量门。
- `feat/*`：新功能分支（如 `feat/p0-glm-agent`）。
- `fix/*`：bug 修复（如 `fix/litellm-deepseek-url`）。
- `docs/*`：纯文档改动（如 `docs/add-spec`）。
- `chore/*`：依赖升级、重构（不改变行为）。
- **禁止**直接 push 到 `main`。
- **禁止**用 `--force` push（除非本人 feature 分支）。

### 4.2 Commit message

- 遵循 Conventional Commits：`<type>(<scope>): <subject>`。
- type 限定清单：`feat` / `fix` / `docs` / `refactor` / `chore` / `test` / `ci` / `build`。
- scope 限定清单：`agent` / `glm` / `deepseek` / `minimax` / `litellm` / `infra` / `spec` / `docs` / `ci`。
- subject MUST ≤ 50 字符，MUST NOT 句末加句号。
- BREAKING CHANGE MUST 在 footer 声明，并触发 ADR（见 §4.5）。

示例：`feat(glm): implement agent card and adk executor`。

### 4.3 质量门（CI 必须全部通过）

| 工具 | 命令 | 失败处理 |
|---|---|---|
| ruff lint | `ruff check .` | 阻塞合并 |
| ruff format | `ruff format --check .` | 阻塞合并 |
| mypy | `mypy agents host orchestrator` | 阻塞合并 |
| pytest 单测 | `pytest -m "not e2e"` | 阻塞合并 |
| pytest e2e | `pytest -m e2e`（仅 docker-compose 起来后跑） | 阻塞合并 |

PR 描述 MUST 列出本地执行上述命令的输出摘要。

### 4.4 版本号（SemVer）

- 项目整体版本走 `pyproject.toml` 的 `version` 字段。
- 每个 P 阶段（P0 / P1 / P2...）完成 MUST 升 minor：`0.1.0` → `0.2.0`。
- 紧急 bugfix 升 patch：`0.2.0` → `0.2.1`。
- BREAKING CHANGE 升 major：`0.x.y` → `1.0.0`（P6 生产化时触发）。
- **禁止**跳号（如 0.1.0 → 0.3.0）。

### 4.5 ADR 触发清单

以下场景 MUST 写 ADR（在 `docs/DECISIONS.md` 新增条目）：

- 新增 / 替换核心依赖（如换 LLM 框架、换编排引擎）。
- 升级核心依赖主版本（如 ADK 1.x → 2.x）。
- 修改 SPEC.md 的 MUST 条款。
- 修改 ARCHITECTURE.md 的组件拓扑或端口分配。
- 引入新的外部服务（如 P4 加 Langfuse）。

以下场景 SHOULD 写 ADR：

- 选用某个库但备选有 2 个以上。
- 删除现有功能 / 模块。

### 4.6 文档同步

- 修改 SPEC.md MUST 在同 PR 内同步影响到的代码或测试。
- 修改代码 MAY 反推 SPEC，但 MUST 在 PR 描述说明 spec 同步策略。
- 修改 ARCHITECTURE.md / CODESTYLE.md MUST 检查是否与 SPEC 冲突。

### 4.7 测试要求

| 类型 | 范围 | 必须 |
|---|---|---|
| 单元测试 | 每个 Agent 的 `build_card` / `build_executor` | SHOULD |
| 契约测试 | Agent Card JSON schema、A2A 方法存在性 | MUST（P0-5 补） |
| e2e 测试 | `tests/test_p1_e2e.py` 起所有容器 + 真实 LLM 调用 | MUST（P1 完成时） |
| 不测 | LiteLLM 内部、ADK 内部、第三方库 | — |

测试命名：`test_<动作>_<预期>`，如 `test_send_message_returns_completed_task`。

---

## 5. 变更日志

### v0.8.0 — 2026-06-08
- P6 完成：Kubernetes 部署清单（Kustomize）。
- 新增 §3.10 K8s 部署清单 spec（部署拓扑、健康检查三件套、PVC、安全基线、ConfigMap/Secret 分离、
  NetworkPolicy、滚动升级与 PDB、Kustomize 入口、Ingress 占位、验证清单）。
- 新增 `infra/k8s/` 目录（14 个 yaml 文件）：namespace / rbac / configmap / pvc /
  litellm / 3 agents / orchestrator / 3 mcp / langfuse 6 组件 / open-webui /
  network policies / pdb。
- 新增 `kustomization.yaml` 入口；新增 `secrets.yaml.example` Secret 模板。
- 网络安全：default-deny-all + 7 条显式 allow NetworkPolicy（按 component label 隔离）。
- 健康检查：所有 workload 配 startup + liveness + readiness 三探针；worker 用 exec 探针。
- 资源调度：5 个 HPA（LiteLLM / 3 Agent / Orchestrator）自动扩缩；5 个 PDB 保障关键服务 ≥ 1 副本。
- 新增 `docs/MIGRATION.md` §3（P6 K8s 迁移指南）+ §4（生产化 TODO 清单）。
- 新增 `tests/contract/test_k8s_manifests.py`（契约测试 18 项）：YAML 语法、namespace 一致性、
  label 完整、健康检查三件套、资源配额、安全基线、镜像 tag 锁版本、PDB 覆盖、
  Kustomize 入口完整性、NetworkPolicy 完整性、Secret 模板完整性。
- `.gitignore` 新增 K8s Secret 忽略规则（`infra/k8s/secrets.yaml` 等）。
- `pyproject.toml` 升 version 0.8.0；新增 `p6_e2e` / `contract` pytest markers。

### v0.7.0 — 2026-06-08
- P5 完成：Open WebUI 前端集成。
- 新增 §3.9 Open WebUI 集成 spec（含服务端口、OpenAI 兼容层契约、model 字段语义、
  流式协议契约、与 OpenAI 官方差异、错误码）。
- Orchestrator 新增 OpenAI 兼容端点：`/v1/models`、`/v1/chat/completions`（含流式）。
- 新增 `orchestrator/openai_compat.py`：Pydantic schema + 转换函数（与 OpenAI 官方对齐）。
- 鉴权：Bearer Token 校验（`ORCHESTRATOR_API_KEY` 或 `LITELLM_MASTER_KEY` 兜底），
  与 Open WebUI 的 `OpenAI API Key` 字段对齐。
- docker-compose 新增 `open-webui` 服务（端口 8080），通过 `OPENAI_API_BASE_URL`
  指向 Orchestrator 内网地址。
- 新增 `frontend/` 目录（独立 compose、env 模板、配置文档），便于"接已有
  orchestrator"场景。
- 新增测试：
  - `tests/contract/test_openai_compat.py`：纯契约测试（schema / 转换函数 / 路由决策）
  - `tests/test_p5_e2e.py`：真实联调测试（Open WebUI 探活 + 4 个 OpenAI 端点 e2e + 端到端）
  - `tests/conftest.py` 第 5 轮探活：Open WebUI 不可达时自动 skip P5 e2e

### v0.6.0 — 2026-06-08
- P4 完成：Langfuse v3 自托管可观测性接入。
- 新增 §3.8 Langfuse 集成 spec（含端口、认证、trace 接入点、OTEL 端点配置、401 Bug 防御）。
- docker-compose 新增 6 个 Langfuse 子服务（web / worker / postgres / clickhouse / redis / minio），端口 3000 / 5432 / 8123 / 6379 / 9000。
- Trace 接入：3 Agent 用 `GoogleADKInstrumentor`、LiteLLM 用 `langfuse_otel` 回调、Orchestrator 用 `@trace_node` 装饰器（双接入 + 自定义）。
- 认证方案：开发环境用 `LANGFUSE_INIT_*` 预置默认 PK/SK（`pk-lf-local` / `sk-lf-local`），生产环境（P6）再升级。
- 新增 `observability/` 目录：`langfuse_client.py`（封装 `get_client` + `auth_check`）+ `tracing.py`（`@trace_node` 装饰器）。
- pyproject.toml 新增 `langfuse>=3.0.0,<4.0.0` + `openinference-instrumentation-google-adk>=0.1.0`。
- 新增 `tests/contract/test_langfuse_client.py`（mock OTEL exporter 守护端点配置 + auth_check）。
- 新增 `tests/test_p4_e2e.py`（发请求 + 查 Langfuse API 确认 trace 出现）。
- `tests/conftest.py` 扩展第 4 轮探活：Langfuse web `/api/public/health`。
- 端口段扩展：3000（沿用 P5 Open WebUI 同端口错开开发窗口）。
- 关联 ADR：ADR-0008（Langfuse 可观测性接入策略）。

### v0.5.0 — 2026-06-08
- P3 完成：MCP 工具生态接入，3 个 Agent 从纯对话升级为可调用工具。
- 新增 3 个 MCP server（FastMCP + Streamable HTTP）：filesystem（自研，沙箱化）/ fetch（复用官方 `mcp-server-fetch` + wrapper）/ shell（自研，allowlist + 30s timeout）。
- 新增 §3.7 MCP 工具生态 spec（含端口、工具契约、安全约束、Agent 接入矩阵）。
- 端口段扩展：12101 / 12102 / 12103（沿用 12000-12099 段）。
- 安全约束：Filesystem 路径逃逸防护（realpath + 前缀校验）、Shell 命令 allowlist + 元字符黑名单 + subprocess shell=False。
- Agent 按人设分配工具：GLM/DeepSeek 用 filesystem 只读 + fetch；MiniMax 用 filesystem 读写 + shell。
- 新增 `tests/contract/test_mcp_filesystem.py` / `test_mcp_fetch.py` / `test_mcp_shell.py`（含安全 payload 覆盖）。
- 新增 `tests/test_p3_e2e.py`（3 Agent 真实调用 MCP tool 的 e2e）。
- 关联 ADR：ADR-0007（MCP 工具生态接入策略）。

### v0.4.0 — 2026-06-08
- P2 完成：`orchestrator/` 实装 LangGraph StateGraph 编排引擎 + FastAPI Host。
- 编排模式：DIRECT（单 Agent）+ TASK_DECOMPOSITION（多 Agent 并行）。
- 路由策略：关键词启发式 + DECOMPOSITION 正则强匹配（P2.1 升级到 LLM 路由）。
- A2A 调用方式：JSON-RPC `message/send`（orchestrator 不绕过 A2A 协议层）。
- HTTP 端点：`POST /v1/orchestrate` / `POST /v1/orchestrate/trace` / `GET /health`。
- 新增 `tests/contract/test_classifier.py` / `test_aggregator.py` / `test_a2a_client.py` / `test_graph.py`（164 个测试）。
- 新增 `tests/test_p2_e2e.py`（9 个真实 LLM e2e 测试）。
- `infra/docker-compose.yml` 新增 orchestrator 服务（端口 12080，depends_on 三 agent healthy）。
- `tests/conftest.py` 扩展探活：orchestrator 不通时单独 skip P2 e2e。
- 修复 `agents/base_agent.py` 6 个 mypy 错误（camelCase→snake_case、TaskState.working、InMemorySessionService type:ignore）。
- 修复 `agents/Dockerfile` 2 个 P0 遗留 bug（CMD 占位符、USER 顺序）。
- 关联 ADR：无新增（沿用 ADR-0006 SPEC SSoT）。

### v0.3.0 — 2026-06-07
- P1 完成：`agents/deepseek_agent/` 与 `agents/minimax_agent/` 实装。
- DeepSeek 角色定位：逻辑推理型（`deepseek-v4-pro` 模型）。
- MiniMax 角色定位：代码工程型（`MiniMax-M3` 模型）。
- 同步 `.env.prod` / `.env.example` / `infra/litellm/config.yaml` 三处模型名一致。
- 扩展 `tests/contract/test_agent_card.py` 至 59 个参数化测试（覆盖三 Agent）。
- 新增 `tests/test_p1_e2e.py`（9 个真实 LLM e2e 测试：3 Agent × 3 端点）。
- 关联 ADR：无新增。

### v0.2.0 — 2026-06-05
- P0-4 完成：`agents/base_agent.py` 实现 `BaseAgent` 抽象 + 默认 `_BaseAgentExecutor`。
- P0-5 完成：`agents/glm_agent/` 实装（agent.py / __main__.py / generate_card.py / agent.json）。
- 新增 `tests/contract/test_agent_card.py` 守护 SPEC §1.1 / §2.2。

### v0.1.0 — 2026-06-05
- 初稿，覆盖 §0–§4 全部章节。
- 对应 P0-2 / P0-3 阶段产出。
- 关联 ADR-0006（采纳 SPEC.md 作为项目规范 SSoT）。

---

**最后更新**：2026-06-08（v0.6.0）
**维护者**：a2a-prod 团队
