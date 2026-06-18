# a2a-prod

> 生产级 A2A 多 Agent 协作系统 — Google ADK + LiteLLM + a2a-sdk + LangGraph
>
> **当前版本**：v0.8.1 · **阶段**：P0~P6 + P2.1 SDLC Workflow 全部完成
> **状态**：✅ 所有里程碑已完成，等待 P6.1 生产化增强

---

## 📋 目录

- [这是什么？](#-这是什么)
- [系统架构](#-系统架构)
- [技术栈](#-技术栈)
- [编排模式（Orchestration Modes）](#-编排模式orchestration-modes)
- [SDLC 研发工作流](#-sdlc-研发工作流)
- [快速开始](#-快速开始)
- [使用指南](#-使用指南)
  - [Open WebUI（推荐）](#open-webui推荐)
  - [Orchestrator API](#orchestrator-api)
  - [直接调用 Agent](#直接调用-agent)
- [项目结构](#-项目结构)
- [端口规划](#-端口规划)
- [测试](#-测试)
- [部署方式](#-部署方式)
  - [方式 A：docker-compose（本地开发）](#方式-adocker-compose本地开发)
  - [方式 B：Kubernetes（生产）](#方式-bkubernetes生产)
- [阶段路线图](#-阶段路线图)
- [文档导航](#-文档导航)
- [License](#license)

---

## 🧭 这是什么？

**a2a-prod** 是一个**生产级多 Agent 协作系统**，让三个国产大模型（GLM-5、DeepSeek、MiniMax-M3）在标准 **A2A (Agent-to-Agent) 协议**下协同工作。

### 核心定位

```
让三个国产大模型在标准 A2A 协议下可被发现、可流式对话、可被编排，
且整个系统任意一层都能被业界主流开源方案替换。
```

### 解决了什么问题？

| 问题 | a2a-prod 方案 |
|---|---|
| 各家模型 API 不统一 | LiteLLM 统一为 OpenAI 兼容接口 |
| 缺乏标准 Agent 通信协议 | Google A2A Protocol + a2a-sdk |
| Agent 各自为战，无法协作 | LangGraph 编排引擎（4 种模式） |
| 缺少工具调用能力 | MCP (Model Context Protocol) 工具生态 |
| 系统观测困难 | Langfuse v3 自托管 + OpenTelemetry |
| 没有用户界面 | Open WebUI 前端 + OpenAI 兼容 API |

---

## 🏗 系统架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Open WebUI (P5)                              │
│                    用户前端 · http://localhost:8080                   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ OpenAI 兼容 API
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Orchestrator (P2) :12080                          │
│   LangGraph + FastAPI · 编排引擎 · 4 种编排模式                     │
│                                                                      │
│   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌────────────────┐  │
│   │ DIRECT   │   │DECOMPOSE │   │WORKFLOW  │   │ OpenAI 兼容层   │  │
│   │ 单 Agent │   │ 分解并行 │   │  SDLC    │   │ /v1/chat/...   │  │
│   └──────────┘   └──────────┘   └──────────┘   └────────────────┘  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ A2A Protocol (JSON-RPC 2.0 / SSE)
          ┌────────────────────┼──────────────────────────┐
          ▼                    ▼                          ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────────────┐
│   GLM Agent      │ │  DeepSeek Agent  │ │    MiniMax Agent         │
│   :12001         │ │  :12002          │ │    :12003                │
│   中文理解生成    │ │  逻辑推理/文档    │ │   代码编写/工程           │
│   ADK + a2a-sdk  │ │  ADK + a2a-sdk   │ │   ADK + a2a-sdk          │
└──┬────┬────┬─────┘ └──┬────┬────┬─────┘ └──┬────┬────┬────────────┘
   │    │    │           │    │    │           │    │    │
   │ FS │Fet │           │ FS │Fet │           │ FS │Shl │Fet
   │只读│    │           │只读│    │           │读写│    │
   ▼    ▼    ▼           ▼    ▼    ▼           ▼    ▼    ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│Filesystem│ │  Fetch   │ │  Shell   │ ← MCP Server (P3)
│MCP :12101│ │MCP :12102│ │MCP :12103│
│ 沙盒文件  │ │ URL 抓取 │ │ 命令执行  │
└──────────┘ └──────────┘ └──────────┘
         │          │          │
         ▼          ▼          ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     LiteLLM Proxy (P0) :4000                         │
│                统一三家国产模型为 OpenAI 兼容接口                      │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ HTTPS
          ┌──────────────────┼──────────────────────┐
          ▼                  ▼                      ▼
     GLM API           DeepSeek API           MiniMax API
   (智谱开放平台)      (deepseek.com)        (minimax.chat)

┌──────────────────────────────────────────────────────────────────────┐
│               Langfuse v3 自托管 (P4) :3000                          │
│  OTEL Trace 收集 · 可观测性 · 6 组件栈                               │
│  (web/worker/postgres/clickhouse/redis/minio)                       │
└──────────────────────────────────────────────────────────────────────┘
```

### 关键组件职责

| 组件 | 职责 | 技术实现 |
|---|---|---|
| **GLM Agent** | 中文理解、内容生成、知识问答 | Google ADK + a2a-sdk |
| **DeepSeek Agent** | 逻辑推理、技术文档、方案设计 | Google ADK + a2a-sdk |
| **MiniMax Agent** | 代码编写、工程实现、单元测试 | Google ADK + a2a-sdk |
| **Orchestrator** | 请求路由、任务分解、结果聚合 | LangGraph + FastAPI |
| **LiteLLM** | 统一三家模型 API 为 OpenAI 兼容格式 | LiteLLM Proxy |
| **Filesystem MCP** | 沙盒文件读写（路径逃逸防护） | FastMCP (Streamable HTTP) |
| **Fetch MCP** | URL 抓取 + HTML→Markdown 转换 | httpx + html2text |
| **Shell MCP** | 受限命令执行（allowlist 白名单） | subprocess(shell=False) |
| **Langfuse** | 可观测性、Trace 收集与可视化 | Langfuse v3 + OpenTelemetry |
| **Open WebUI** | 用户聊天界面 | Open WebUI (ghcr.io) |

---

## 🛠 技术栈

### 核心框架

| 技术 | 用途 | 版本 |
|---|---|---|
| [Google ADK](https://github.com/google/adk-python) | Agent 开发框架、A2A 协议暴露 | ≥1.34.0 |
| [a2a-sdk](https://github.com/google/a2a-sdk) | A2A Agent-to-Agent 协议 SDK | ≥0.3.4 |
| [LangGraph](https://langchain-ai.github.io/langgraph/) | 多 Agent 编排引擎（StateGraph） | ≥0.4.0 |
| [FastAPI](https://fastapi.tiangolo.com/) | HTTP 服务框架 | ≥0.115.0 |
| [LiteLLM](https://litellm.ai/) | LLM API 统一代理 | 官方镜像 |
| [MCP](https://modelcontextprotocol.io/) | Model Context Protocol 工具生态 | ≥1.5.0 |
| [OpenTelemetry](https://opentelemetry.io/) | 分布式追踪与可观测性 | ≥1.33.1 |
| [Langfuse](https://langfuse.com/) | LLM 可观测性平台（自托管） | v3 |
| [Open WebUI](https://openwebui.com/) | 用户聊天前端 | `:main` |

### 开发工具

| 工具 | 用途 |
|---|---|
| Python 3.12 | 运行环境 |
| uv | 包管理器（推荐，比 pip 快 5-10 倍） |
| ruff | Linter + Formatter |
| mypy | 静态类型检查（`--strict`） |
| pytest | 测试框架（含契约测试 + e2e 测试） |
| structlog | 结构化日志 |
| pydantic-settings | 配置管理（环境变量注入） |
| Docker Compose | 本地容器编排 |
| Kubernetes + Kustomize | 生产部署 |

### 支持的模型

| 模型提供商 | 模型 | Agent 角色 |
|---|---|---|
| **GLM (智谱)** | `glm-5` | 中文理解与生成（产品经理 + 技术总监） |
| **DeepSeek** | `deepseek-chat` | 逻辑推理与文档（需求分析 + 方案设计） |
| **MiniMax** | `MiniMax-M3` | 代码实现与工程（主力代码编写） |

---

## 🔄 编排模式（Orchestration Modes）

Orchestrator 支持 **4 种编排模式**，由 Classifier 根据用户输入自动决策：

### 1. DIRECT（直接路由）
单 Agent 响应，透传不拼接。

```
用户 → Classifier → GLM/DeepSeek/MiniMax → 用户
```

**触发条件**：关键词匹配（如 `glm`、`deepseek`、`minimax` 指定 Agent）。

### 2. DECOMPOSITION（任务分解）
复杂问题拆成多个子任务，并行分配给最适合的 Agent，最后聚合结果。

```
用户 → Classifier → _decompose()
                    ├── 子任务 1 → DeepSeek Agent
                    ├── 子任务 2 → GLM Agent
                    └── 子任务 3 → MiniMax Agent
                    → aggregate() → 用户
```

**触发条件**：含"对比"/"比较"关键词或多 Agent 路由。

### 3. NEGOTIATION（协商模式）
多 Agent 并行调用，异常降级（某个 Agent 失败不阻塞其他）。

### 4. WORKFLOW（工作流模式）
预定义的步骤流水线，当前仅支持 SDLC 研发协作工作流（见下方）。

---

## 📋 SDLC 研发工作流

P2.1 核心特性 — **自动化软件开发生命周期**：

```
用户需求
  │
  ▼
┌──────────────────────────────────────────────────┐
│ Step 1: DeepSeek Agent                           │
│  产出：技术方案文档 (spec.md)                      │
│  Prompt: DEEPSEEK_SPEC_PROMPT                    │
├──────────────────────────────────────────────────┤
│ Step 2: GLM Agent                                │
│  产出：技术设计规范 (tech-design.md)                │
│  Prompt: GLM_TECH_DESIGN_PROMPT                  │
├──────────────────────────────────────────────────┤
│ Step 3: MiniMax Agent                            │
│  产出：代码实现 + 自测                             │
│  Prompt: MINIMAX_CODE_PROMPT                     │
│  MCP 工具：Filesystem(读写) + Shell(pytest)       │
├──────────────────────────────────────────────────┤
│ 遇阻 [NEED_HELP] → 反馈 GLM Agent (Step 2)      │
│ 单轮反馈上限：N=2                                 │
└──────────────────────────────────────────────────┘
  │
  ▼
用户获得：spec + 技术设计 + 代码 + 测试结果
```

**三层角色边界防御**：
1. DeepSeek **禁止**写代码（只有读文件权限）
2. MiniMax **仅限**写代码（不得修改 spec）
3. 步骤强顺序，不可跳过

---

## 🚀 快速开始

### 前置条件

- Python 3.12+
- Docker Desktop + Docker Compose
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip
- 三家国产模型 API Key（GLM / DeepSeek / MiniMax）

### 1. 克隆项目

```bash
git clone https://github.com/Commit-Cafe/a2a-prod.git
cd a2a-prod
```

### 2. 创建虚拟环境

```bash
# 用 uv（推荐，快）
uv venv .venv-prod --python 3.12
source .venv-prod/bin/activate   # Linux/Mac
# 或 Windows: .venv-prod\Scripts\Activate.ps1

# 或用 pip
python -m venv .venv-prod
source .venv-prod/bin/activate
```

### 3. 安装依赖

```bash
uv pip install -e ".[dev]"
# 或
pip install -e ".[dev]"
```

### 4. 配置环境变量

```bash
cp .env.example .env.prod
# 编辑 .env.prod 填入三家 API Key
```

关键环境变量：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `GLM_API_KEY` | GLM API Key | — |
| `DEEPSEEK_API_KEY` | DeepSeek API Key | — |
| `MINIMAX_API_KEY` | MiniMax API Key | — |
| `LITELLM_MASTER_KEY` | LiteLLM 主密钥 | — |
| `GLM_MODEL` | GLM 模型名 | `glm-5` |
| `DEEPSEEK_MODEL` | DeepSeek 模型名 | `deepseek-chat` |
| `MINIMAX_MODEL` | MiniMax 模型名 | `MiniMax-M3` |

### 5. 启动全部服务

```bash
docker compose --env-file .env.prod up -d
```

这会启动：LiteLLM + 3 个 Agent + Orchestrator + 3 个 MCP Server + Langfuse (6 子服务) + Open WebUI。

### 6. 验证

```bash
# 检查所有服务是否健康
docker compose ps

# 检查 LiteLLM 模型路由
curl http://localhost:4000/v1/models

# 检查 GLM Agent Card
curl http://localhost:12001/.well-known/agent.json

# 检查 Orchestrator 健康
curl http://localhost:12080/health

# 运行 e2e 测试
pytest -m e2e -v
```

### 7. 打开 UI

| 服务 | URL | 说明 |
|---|---|---|
| Open WebUI | http://localhost:8080 | 用户聊天界面 |
| Langfuse Dashboard | http://localhost:3000 | Trace 观测 |
| Orchestrator Swagger | http://localhost:12080/docs | API 文档 |

---

## 💡 使用指南

### Open WebUI（推荐）

1. 打开 http://localhost:8080
2. 注册账号（首次使用）
3. 在模型下拉框中选择：
   - `auto` — 自动路由（Orchestrator 决定最佳 Agent）
   - `glm-agent` — 强制使用 GLM
   - `deepseek-agent` — 强制使用 DeepSeek
   - `minimax-agent` — 强制使用 MiniMax

### Orchestrator API

Orchestrator 提供 OpenAI 兼容端点，可被任意 OpenAI SDK 客户端调用：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:12080/v1",
    api_key="your-litellm-master-key",
)

# 列出可用模型
models = client.models.list()
for m in models:
    print(m.id)  # auto, glm-agent, deepseek-agent, minimax-agent

# 聊天完成（自动路由）
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "写一个 Python 计算器"}],
    stream=True,
)
for chunk in response:
    print(chunk.choices[0].delta.content or "", end="")
```

#### 流式请求

```bash
curl -X POST http://localhost:12080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "比较 Python 和 Go 的优劣"}],
    "stream": true
  }'
```

#### 触发 SDLC 工作流

```bash
curl -X POST http://localhost:12080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "使用工作流模式写一个计算器程序"}]
  }'
```

当 Classifier 检测到"工作流"相关关键词时，自动进入 SDLC WORKFLOW 模式。

### 直接调用 Agent

也可以绕过 Orchestrator，直接向 Agent 发送 A2A 协议请求：

```bash
# 同步请求
curl -X POST http://localhost:12001/message/send \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "req-001",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "你好"}]
      }
    }
  }'

# 流式 SSE 请求
curl -X POST http://localhost:12001/message/stream \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "req-002",
    "method": "message/stream",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "写一首诗"}]
      }
    }
  }'
```

---

## 📁 项目结构

```
a2a-prod/
├── agents/                        # Agent 实现（ADK + a2a-sdk）
│   ├── base_agent.py              # BaseAgent 抽象基类
│   ├── Dockerfile                 # 共享 Dockerfile（build-arg 切换模块）
│   ├── glm_agent/                 # GLM Agent（P0）
│   │   ├── agent.py / agent.json / generate_card.py
│   ├── deepseek_agent/            # DeepSeek Agent（P1）
│   │   ├── agent.py / agent.json / generate_card.py
│   └── minimax_agent/             # MiniMax Agent（P1）
│       ├── agent.py / agent.json / generate_card.py
├── orchestrator/                  # LangGraph 编排引擎（P2）
│   ├── graph.py                   # StateGraph 主图
│   ├── state.py                   # OrchestrationState 定义
│   ├── classifier.py              # 请求分类器（模式选择）
│   ├── executor.py                # Agent 执行器
│   ├── aggregator.py              # 结果聚合器
│   ├── a2a_client.py              # A2A JSON-RPC 客户端
│   └── openai_compat.py           # OpenAI 兼容层（P5）
├── host/                          # Orchestrator 入口（P2）
│   └── __init__.py
├── mcp_servers/                   # MCP 工具生态（P3）
│   ├── Dockerfile                 # 通用 MCP Dockerfile
│   ├── filesystem/server.py       # 文件读写（沙盒安全）
│   ├── fetch/server.py            # URL 抓取
│   └── shell/server.py            # 命令执行（allowlist 白名单）
├── observability/                 # 可观测性（P4）
│   ├── langfuse_client.py         # Langfuse 客户端封装
│   ├── tracing.py                 # @trace_node 装饰器
│   ├── setup.py                   # 启动初始化
│   └── litellm_entrypoint.py      # LiteLLM 入口包装
├── frontend/                      # Open WebUI 集成（P5）
│   ├── README.md                  # 前端配置文档
│   ├── docker-compose.yml         # 独立 compose 文件
│   └── config/                    # 模型映射 / System Prompt
├── infra/                         # 基础设施
│   ├── docker-compose.yml         # 主 compose 文件
│   ├── litellm/
│   │   ├── config.yaml            # 模型路由配置
│   │   └── Dockerfile             # 自定义 LiteLLM 镜像
│   └── k8s/                       # Kubernetes 部署（P6）
│       ├── kustomization.yaml     # Kustomize 入口
│       ├── 00-namespace.yaml      # 命名空间
│       ├── 10-litellm.yaml        # LiteLLM 部署
│       ├── 20-agents.yaml         # 3 个 Agent
│       ├── 30-orchestrator.yaml   # Orchestrator
│       ├── 40-mcp.yaml            # 3 个 MCP Server
│       ├── 50-langfuse.yaml       # Langfuse 6 组件
│       ├── 60-open-webui.yaml     # Open WebUI
│       ├── 70-network-policies.yaml # 网络安全策略
│       └── 80-pdb.yaml            # Pod 中断预算
├── tests/                         # 测试
│   ├── contract/                  # 契约测试（无需 Docker）
│   ├── test_p1_e2e.py ~ test_p5_e2e.py  # e2e 测试
│   └── conftest.py                # 共享 Fixture
├── workspace/                     # MCP 沙盒目录
│   └── samples/                   # 示例代码
├── scripts/                       # 工具脚本
│   ├── check_env.py               # API Key 预检
│   ├── check_ports.ps1            # 端口冲突检查
│   └── healthcheck.py             # 容器健康检查
├── docs/                          # 文档
│   ├── NORTH_STAR.md              # 项目愿景与方向
│   ├── SPEC.md                    # 项目规范（SSoT）
│   ├── ARCHITECTURE.md            # 架构说明
│   ├── DECISIONS.md               # ADR 架构决策
│   ├── CODESTYLE.md               # 代码风格规范
│   └── MIGRATION.md               # 迁移指南
├── .env.example                   # 环境变量模板
├── pyproject.toml                 # 项目配置
└── README.md                      # 本文件
```

---

## 🔌 端口规划

| 服务 | 端口 | 阶段 | 说明 |
|---|---|---|---|
| LiteLLM Proxy | 4000 | P0 | 统一 LLM 入口 |
| GLM Agent | 12001 | P0 | ADK A2A Server |
| DeepSeek Agent | 12002 | P1 | ADK A2A Server |
| MiniMax Agent | 12003 | P1 | ADK A2A Server |
| **Orchestrator** | **12080** | P2 | **编排入口 + OpenAI 兼容端点** |
| Filesystem MCP | 12101 | P3 | 沙盒文件读写 |
| Fetch MCP | 12102 | P3 | URL 抓取 |
| Shell MCP | 12103 | P3 | 受限命令执行 |
| Langfuse Web | 3000 | P4 | Trace Dashboard |
| Open WebUI | 8080 | P5 | 用户前端 |

> **端口隔离**：12000-12099 段全部归 a2a-prod 使用，与其他项目完全无重叠。

---

## 🧪 测试

项目拥有完善的测试体系，覆盖 **契约测试 + 端到端测试**。

### 测试标记

| 标记 | 类型 | 说明 |
|---|---|---|
| `unit` | 单元测试 | 纯逻辑测试，无需 Docker |
| `contract` | 契约测试 | Schema / 接口契约守护，无需 Docker |
| `e2e` | 端到端测试 | 需要 docker-compose 启动 |
| `p2_e2e` | P2 编排 e2e | 需要 Orchestrator |
| `p2_1_e2e` | SDLC 工作流 e2e | 需要 3 个 Agent + Orchestrator |
| `p3_e2e` | MCP e2e | 需要 3 个 MCP + 3 个 Agent |
| `p4_e2e` | Langfuse e2e | 需要 langfuse-web |
| `p5_e2e` | Open WebUI e2e | 需要 Open WebUI + Orchestrator |

### 运行测试

```bash
# 所有契约测试（无需 Docker，快速）
pytest -m "contract" -v

# 所有单元测试
pytest -m "unit" -v

# 完整测试套件（需要 docker-compose 启动）
pytest -v

# 带覆盖率
pytest --cov=agents --cov=orchestrator --cov=mcp_servers --cov=observability
```

### 代码质量门禁

```bash
# Lint
ruff check .

# 格式检查
ruff format --check .

# 类型检查
mypy agents host orchestrator mcp_servers observability
```

---

## 🐳 部署方式

### 方式 A：docker-compose（本地开发）

```bash
# 启动全部服务
docker compose --env-file .env.prod up -d

# 查看日志
docker compose logs -f

# 停止
docker compose down
```

资源要求（P4 起 Langfuse 完整版）：**至少 4 核 CPU + 16 GiB 内存**。

### 方式 B：Kubernetes（生产）

项目提供完整的 Kustomize 部署清单（P6），包含 14 个 YAML 文件：

```bash
# 1. 创建 k3d 集群
k3d cluster create a2a-prod --agents 2 --servers 1

# 2. 准备 Secrets
cp infra/k8s/secrets.yaml.example infra/k8s/secrets.yaml
# 编辑 secrets.yaml 填入所有敏感信息

# 3. 部署全部
kubectl apply -k infra/k8s/

# 4. 等待就绪
kubectl -n a2a-prod wait --for=condition=ready pod -l app.kubernetes.io/part-of=a2a-prod --timeout=600s

# 5. 暴露本地端口
kubectl -n a2a-prod port-forward svc/orchestrator 12080:12080 &
kubectl -n a2a-prod port-forward svc/open-webui 8080:8080 &
kubectl -n a2a-prod port-forward svc/langfuse-web 3000:3000 &
```

K8s 部署包含的完整安全特性：

| 特性 | 说明 |
|---|---|
| **HPA** | 5 个自动扩缩（LiteLLM / 3 Agent / Orchestrator） |
| **PDB** | 5 个 Pod 中断预算保障关键服务 ≥ 1 副本 |
| **NetworkPolicy** | 默认拒绝 + 7 条显式放行规则 |
| **安全基线** | `runAsNonRoot`、`allowPrivilegeEscalation=false`、`capabilities.drop: [ALL]` |
| **健康检查** | 三探针（startup + liveness + readiness） |
| **滚动升级** | RollingUpdate + maxSurge=1, maxUnavailable=0 |

详见 [`infra/k8s/README.md`](infra/k8s/README.md) 与 [`docs/MIGRATION.md`](docs/MIGRATION.md)。

---

## 🗺 阶段路线图

### ✅ 已完成

| 阶段 | 内容 | 版本 |
|---|---|---|
| **P0** | 三 Agent ADK + LiteLLM 统一路由 + 共享基类 + Agent Card | v0.1.0 |
| **P1** | DeepSeek / MiniMax Agent 实装 + 容器化 + e2e 测试 | v0.3.0 |
| **P2** | LangGraph 编排引擎（4 模式）+ FastAPI Host | v0.4.0 |
| **P2.1** | SDLC 研发协作工作流（DeepSeek→GLM→MiniMax + 反馈） | v0.4.x |
| **P3** | MCP 工具生态（filesystem / fetch / shell）+ 安全约束 | v0.5.0 |
| **P4** | Langfuse v3 自托管 + OTEL trace 双接入 | v0.6.0 |
| **P5** | Open WebUI + OpenAI 兼容层（`/v1/chat/completions`） | v0.7.0 |
| **P6** | K8s 部署清单（14 yaml / HPA / PDB / NetworkPolicy / 安全基线） | v0.8.0 |

### 📅 规划中（P6.1+ 生产化）

- TLS / Ingress / cert-manager
- Sealed Secrets / External Secrets
- Prometheus / Grafana / Loki 监控栈
- ArgoCD / Flux GitOps 部署
- 多模态 / tool_calls / 真实 token 计数
- 第二个工作流模板

---

## 📚 文档导航

| 文档 | 用途 | 必读指数 |
|---|---|---|
| [docs/NORTH_STAR.md](docs/NORTH_STAR.md) | 项目愿景、做与不做、验收标准 | ⭐⭐⭐ |
| [docs/SPEC.md](docs/SPEC.md) | **项目规范单一事实源**（协议/接口/流程/基础设施） | ⭐⭐⭐ |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构图、组件职责、数据流、演进路线 | ⭐⭐⭐ |
| [docs/DECISIONS.md](docs/DECISIONS.md) | ADR 架构决策记录（技术选型理由） | ⭐⭐ |
| [docs/CODESTYLE.md](docs/CODESTYLE.md) | 代码风格、命名、目录约定 | ⭐⭐ |
| [docs/MIGRATION.md](docs/MIGRATION.md) | K8s 迁移与生产化指南 | ⭐ |

---

## 📄 License

MIT

---

**最后更新**：2026-06-18 · **维护者**：a2a-prod 团队
