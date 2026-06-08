# ARCHITECTURE — a2a-prod 架构说明

> 本文档描述 a2a-prod 的当前架构与各阶段演进路线。
> 任何架构层面的变更必须同步更新本文档 + DECISIONS.md 留 ADR。
>
> 与本文档平级的项目规范见 [SPEC.md](SPEC.md)（协议 / 接口 / 流程 / 基础设施）。
> 当本文档与 SPEC.md 冲突时，以 SPEC.md 为准（见 SPEC.md §0.1 仲裁顺序）。

---

## 1. 当前阶段（P0+P1+P2+P3+P4）目标架构

```
┌─────────────────────────────────────────────────────────────┐
│   开发者 / 测试脚本                                          │
│   (curl / httpx / tests/test_p*_e2e.py)                     │
└────────────────────────────┬────────────────────────────────┘
                             │ A2A Protocol (JSON-RPC 2.0 / SSE)
                             │
               ┌─────────────┴─────────────┐
               │ Orchestrator Host :12080   │
               │ LangGraph + FastAPI (P2)   │
               └─────────────┬─────────────┘
                             │ A2A Protocol
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ GLM Agent    │     │ DeepSeek Agt │     │ MiniMax Agt  │
│ ADK + a2a-sdk│     │ ADK + a2a-sdk│     │ ADK + a2a-sdk│
│ :12001       │     │ :12002       │     │ :12003       │
└──┬───────┬───┘     └──┬───────┬───┘     └──┬───────┬───┘
   │       │            │       │            │       │
   │ MCP   │ MCP        │ MCP   │ MCP        │ MCP   │ MCP
   │(FS/R) │(Fetch)     │(FS/R) │(Fetch)     │(FS/RW)│(Shell)
   ▼       ▼            ▼       ▼            ▼       ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ Filesystem   │  │ Fetch MCP    │  │ Shell MCP    │
│ MCP :12101   │ │ :12102       │  │ :12103       │
│ workspace/   │  │ httpx+html2t │  │ allowlist    │
└──────────────┘  └──────────────┘  └──────────────┘

               ┌──────────────────────────┐
               │ LiteLLM Proxy :4000       │
               │ 统一 LLM 入口             │
               └────────────┬─────────────┘
                            │ HTTPS
       ┌────────────────────┼────────────────────┐
       ▼                    ▼                    ▼
   GLM API            DeepSeek API         MiniMax API

        ┌─────────────────────────────────────────┐
        │ Langfuse v3 自托管 :3000（P4，详见 ADR-0008）│
        │ ├─ langfuse-web (3000)                  │
        │ ├─ langfuse-worker                       │
        │ ├─ postgres (5432) / clickhouse (8123)  │
        │ └─ redis (6379) / minio (9000)           │
        │                                          │
        │ 3 Agent + LiteLLM + Orchestrator 通过   │
        │ OTEL exporter 上报 trace 到 langfuse-web │
        └─────────────────────────────────────────┘
```

### 1.1 关键组件职责

| 组件 | 职责 | 实现技术 |
|---|---|---|
| **GLM/DeepSeek/MiniMax Agent** | 暴露 A2A 协议、接收任务、调用 LLM、流式返回 | Google ADK + a2a-sdk |
| **LiteLLM Proxy** | 统一三家国产模型为 OpenAI 兼容接口 | LiteLLM docker 镜像 |
| **Orchestrator** | LangGraph 编排 4 种模式，FastAPI 入口 | LangGraph + FastAPI |
| **Filesystem MCP** | workspace/ 沙盒内文件读写，路径逃逸防护 | FastMCP (Streamable HTTP) |
| **Fetch MCP** | URL 抓取 + HTML→Markdown 转换 | httpx + html2text |
| **Shell MCP** | 受限命令执行（allowlist + 元字符黑名单） | subprocess(shell=False) |
| **Langfuse v3** | 6 子服务（web/worker/postgres/clickhouse/redis/minio），自托管可观测 | docker.io/langfuse/langfuse:3 |
| **Trace 注入** | 3 Agent: GoogleADKInstrumentor / LiteLLM: langfuse_otel 回调 / Orchestrator: @trace_node | OTEL OTLP |
| **docker-compose** | 一键起所有服务、网络隔离 | docker-compose.yml |

### 1.2 数据流（一次 message/send 请求）

```
client
  → POST :12001/message/send  (JSON-RPC)
  → GLM Agent (ADK Executor)
  → ADK 调用 LLM (OpenAI 兼容)
  → LiteLLM Proxy :4000
  → 路由到 GLM API (open.bigmodel.cn)
  → 流式返回
  → ADK 转为 A2A SSE 事件
  → client 接收 task/update + 最终 task/completed
```

---

## 2. 全阶段演进路线图

### P0 — 基建周（当前）
- [x] NORTH_STAR / ARCHITECTURE / CODESTYLE / DECISIONS 文档
- [x] 项目骨架 + pyproject.toml（含 ADK 1.34 + a2a-sdk 0.3.x 版本基线，见 ADR-0005）
- [x] docker-compose.yml（LiteLLM + 占位三 Agent，位于 infra/）
- [x] LiteLLM config.yaml（GLM/DeepSeek/MiniMax 三家路由，位于 infra/litellm/）
- [x] scripts/ 三脚本：check_env.py / check_ports.ps1 / healthcheck.py
- [x] agents/Dockerfile（三 Agent 共享，通过 build-arg 切换模块）
- [x] .dockerignore
- [x] SPEC.md 项目规范单一事实源（ADR-0006）
- [x] base_agent.py 共享基类（按 SPEC §2.1 实现 BaseAgent + 默认 AgentExecutor）
- [x] GLM Agent 实装 + Agent Card（agents/glm_agent/，含 generate_card.py）
- [x] tests/contract/test_agent_card.py 守护 SPEC §1.1
- [x] P0 验收：LiteLLM → GLM API 真实调用 + A2A message/send 端到端打通（2026-06-05）

### P1 — 三 Agent 落地
- [x] DeepSeek Agent（复用 base_agent）
- [x] MiniMax Agent（复用 base_agent）
- [x] 三容器同时启动，健康检查通过
- [x] e2e 测试脚本 `tests/test_p1_e2e.py`

### P2 — 编排引擎引入
- [x] LangGraph StateGraph 主图
- [x] 4 种编排模式：直接路由 / 任务分解 / 协商 / 工作流
- [x] Orchestrator Host FastAPI 服务

### P3 — 工具生态
- [x] 3 个 MCP Server：filesystem / fetch / shell（FastMCP + Streamable HTTP）
- [x] workspace/ 沙盒目录 + samples 样例代码
- [x] 3 Agent 接入 McpToolset（GLM/DeepSeek: FS 只读 + fetch; MiniMax: FS 全权限 + shell）
- [x] Docker 部署（通用 Dockerfile + docker-compose 新增 3 MCP 服务）
- [x] contract 测试 332 项（安全 / 超时 / allowlist）
- [x] e2e 测试（9 项，含 MCP 直接调用 + Agent×MCP 联调）

### P4 — 可观测性
- [x] Langfuse v3 自托管（6 子服务：web / worker / postgres / clickhouse / redis / minio）
- [x] OTEL trace 接入：3 Agent（GoogleADKInstrumentor）+ LiteLLM（langfuse_otel 回调）+ Orchestrator（@trace_node 装饰器）
- [x] 防御 [GitHub issue #9871](https://github.com/langfuse/langfuse/issues/9871) 401：所有 Agent 容器 `LANGFUSE_HOST` 走 docker 网络名
- [x] INIT 变量自动创建 org/project + 默认 PK/SK（开发环境）
- [x] `observability/` 目录：`langfuse_client.py` + `tracing.py` + `setup.py` + `litellm_entrypoint.py`
- [x] 自定义 `infra/litellm/Dockerfile`（基于官方 LiteLLM 镜像 + 注入 langfuse SDK）
- [x] contract 测试 23 项（环境变量 / mock OTEL exporter / 异常降级）
- [x] e2e 测试 4 项（health 端点 / auth_check / 真实 trace 落盘）

### P5 — 前端 & 用户体验
- [x] Open WebUI docker 服务（`ghcr.io/open-webui/open-webui:main`，端口 8080）
- [x] Orchestrator 暴露 OpenAI 兼容 `/v1/chat/completions` + `/v1/models`（同步 + SSE 流式）
- [x] WebUI 多 Agent 切换（model 下拉框：auto / glm-agent / deepseek-agent / minimax-agent）
- [x] Bearer Token 鉴权（与 `LITELLM_MASTER_KEY` / `ORCHESTRATOR_API_KEY` 同步）
- [x] `orchestrator/openai_compat.py` 独立模块（schema + 转换函数）
- [x] `frontend/` 目录（独立 compose、env 模板、配置文档）
- [x] contract 测试 18 项（schema 对齐 / 转换逻辑 / 路由决策）
- [x] e2e 测试 9 项（Open WebUI 探活 + 4 个端点 e2e + 端到端）

### P6 — 生产化
- [x] K8s manifest（k3d / minikube 验证）
- [x] 健康检查 + Bearer Token 鉴权（startup + liveness + readiness 三探针）
- [x] 滚动升级策略（RollingUpdate + PDB + HPA + NetworkPolicy + 资源配额 + 安全基线）
- [x] `infra/k8s/` 14 个 yaml（Kustomize 入口）
- [x] NetworkPolicy 默认 deny + 7 条显式 allow
- [x] Secret 模板（gitignore secrets.yaml）
- [x] 契约测试 18 项（YAML 语法 / namespace / label / 探针 / 配额 / 安全 / PDB 覆盖 / Kustomize 完整性）

---

## 3. 端口规划总表

| 服务 | 端口 | 阶段 | 说明 |
|---|---|---|---|
| LiteLLM Proxy | 4000 | P0 | 统一 LLM 入口 |
| GLM Agent | 12001 | P0 | ADK A2A Server |
| DeepSeek Agent | 12002 | P1 | ADK A2A Server |
| MiniMax Agent | 12003 | P1 | ADK A2A Server |
| Orchestrator Host | 12080 | P2 | FastAPI 编排入口 |
| Filesystem MCP | 12101 | P3 | workspace/ 沙盒文件读写 |
| Fetch MCP | 12102 | P3 | URL 抓取 + HTML→Markdown |
| Shell MCP | 12103 | P3 | 受限命令执行 |
| Langfuse Web | 3000 | P4 | 自托管 trace UI（dashboard）|
| Langfuse Postgres | 5432 | P4 | 内部 OLTP（仅本机）|
| Langfuse ClickHouse | 8123 | P4 | trace OLAP（仅本机）|
| Langfuse MinIO | 9000 | P4 | S3 兼容对象存储（仅本机）|
| Open WebUI | 8080 | P5 | 用户前端 |

**端口隔离原则**：12000-12099 段全部归 a2a-prod 使用，
与原 a2a-agents（11000-11099）完全无重叠。

---

## 4. 目录结构（含未来预留）

```
a2a-prod/
├── docs/                       # 本目录
│   ├── NORTH_STAR.md
│   ├── ARCHITECTURE.md
│   ├── CODESTYLE.md
│   ├── DECISIONS.md
│   └── MIGRATION.md            # P6 阶段补
├── agents/
│   ├── __init__.py
│   ├── base_agent.py           # P0 共享基类（含 P3 MCP URL 字段）
│   ├── glm_agent/              # P0（P3 加 FS 只读 + fetch）
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   ├── agent.json
│   │   └── generate_card.py
│   ├── deepseek_agent/         # P1（P3 加 FS 只读 + fetch）
│   │   ├── agent.py / agent.json / generate_card.py
│   └── minimax_agent/          # P1（P3 加 FS 全权限 + shell）
│       ├── agent.py / agent.json / generate_card.py
├── host/                       # P2 编排层
│   └── __init__.py
├── orchestrator/               # P2 LangGraph
│   ├── graph.py / server.py / ...
│   └── __init__.py
├── mcp_servers/                # P3 MCP 工具生态
│   ├── Dockerfile              # 通用（MCP_MODULE build-arg）
│   ├── filesystem/             # :12101
│   │   ├── server.py           # read_file/list_directory/write_file/create_directory
│   │   └── __main__.py
│   ├── fetch/                  # :12102
│   │   ├── server.py           # fetch (httpx + html2text)
│   │   └── __main__.py
│   └── shell/                  # :12103
│       ├── server.py           # run_command (allowlist + metachar blacklist)
│       └── __main__.py
├── workspace/                  # P3 沙盒（bind mount 到 MCP 容器）
│   ├── samples/
│   │   ├── calc.py             # 计算器（含故意问题，供代码审查测试）
│   │   └── test_calc.py
│   └── .gitkeep
├── observability/              # P4 Langfuse 可观测性
│   ├── __init__.py
│   ├── langfuse_client.py      # is_enabled / get_client / setup_otlp_env / health_check
│   ├── tracing.py              # @trace_node 装饰器（同步 + 异步）
│   ├── setup.py                # setup_agent() + setup_litellm() 启动入口
│   └── litellm_entrypoint.py   # LiteLLM 容器启动包装（注入 langfuse_otel）
├── frontend/                   # P5 预留
│   └── README.md
├── infra/
│   ├── docker-compose.yml      # P0+P1+P2+P3+P4
│   ├── litellm/
│   │   ├── Dockerfile          # P4 自定义（官方镜像 + 注入 langfuse SDK）
│   │   └── config.yaml         # P0
│   └── k8s/                    # P6 预留
├── tests/
│   ├── __init__.py
│   ├── conftest.py             # P0+P2+P3+P4 四轮探活
│   ├── contract/               # P0+P1+P3+P4 守护测试
│   │   ├── test_agent_card.py
│   │   ├── test_mcp_filesystem.py
│   │   ├── test_mcp_fetch.py
│   │   ├── test_mcp_shell.py
│   │   └── test_langfuse_client.py
│   ├── test_p1_e2e.py          # P1
│   ├── test_p2_e2e.py          # P2
│   ├── test_p3_e2e.py          # P3
│   └── test_p4_e2e.py          # P4
├── scripts/
│   ├── check_env.py            # API Key 预检
│   ├── check_ports.ps1         # 启动前端口检查
│   └── healthcheck.py          # 容器健康检查
├── .env.example                # P0（含 P3 MCP 变量）
├── .env.prod                   # 生产环境变量
├── .gitignore
├── .python-version             # 3.12
├── pyproject.toml              # P0+P3 deps
└── README.md
```

---

## 5. 关键数据契约

### 5.1 Agent Card（A2A 标准）

每个 Agent 必须暴露 `GET /.well-known/agent.json`，最小字段：

```json
{
  "name": "glm-agent",
  "description": "GLM-5.1 中文理解与内容生成 Agent",
  "version": "0.1.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false
  },
  "skills": [
    {
      "id": "chinese-text-generation",
      "name": "中文文本生成",
      "description": "中文理解、内容生成、知识问答",
      "examples": ["写一段关于...的中文介绍"]
    }
  ]
}
```

### 5.2 一次 message/send 请求（A2A 标准）

```json
{
  "jsonrpc": "2.0",
  "id": "req-001",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [{"kind": "text", "text": "你好"}]
    }
  }
}
```

### 5.3 一次 message/stream 响应（SSE）

```
event: task/update
data: {"state": "working"}

event: task/update
data: {"state": "working", "message": {...}}

event: task/completed
data: {"state": "completed", "message": {...}}
```

---

## 6. 错误处理约定

| 错误类型 | 处理策略 |
|---|---|
| LLM API 超时 | 重试 3 次，指数退避，最终返回 A2A `internal error` |
| LLM API Key 无效 | 启动时 check_api_keys.py 预检，运行时返回 `unauthorized` |
| Agent Card 不合规 | 启动时自检失败，容器不退出进入 retry |
| LiteLLM 路由失败 | 返回 502，记录 trace |
| Agent 间通信失败（P2+） | LangGraph 节点 retry，3 次失败转人机回路 |

---

**最后更新**：2026-06-08（**P0+P1+P2+P3+P4 全部完成** — P4 Langfuse 可观测性：6 子服务 + ADK+LiteLLM 双接入 + 23 contract + 4 e2e）
