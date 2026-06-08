# a2a-prod

> 生产级 A2A 多 Agent 协作系统 — 基于 Google ADK + LiteLLM + a2a-sdk
>
> **当前阶段**：P0+P1+P2+P3+P4+P5+P6（K8s 部署清单已实装）
> **状态**：✅ P0~P6 全部完成 / 等待 P6.1（生产化）
> **版本**：v0.8.0

---

## 这是什么？

a2a-prod 是 [原 a2a-agents](D:\trae_Dproject3\a2a) 的生产化重写版本，
让 GLM-5.1 / DeepSeek / MiniMax-M3 三个国产大模型在标准 A2A 协议下：

- **可被发现**（标准 Agent Card）
- **可流式对话**（SSE）
- **可被编排**（P2 引入 LangGraph）

与原项目**完全隔离**（独立路径 / 独立 venv / 独立端口段 / 独立 Docker network）。

---

## 快速开始

### 前置条件
- Python 3.12+
- Docker Desktop + Docker Compose
- uv（推荐）或 pip
- 三家国产模型 API Key（GLM / DeepSeek / MiniMax）

### 1. 克隆 / 进入项目
```powershell
cd D:\trae_Dproject4\a2a-prod
```

### 2. 创建虚拟环境
```powershell
# 用 uv（推荐，快）
uv venv .venv-prod --python 3.12
.venv-prod\Scripts\Activate.ps1

# 或用 pip
python -m venv .venv-prod
.venv-prod\Scripts\Activate.ps1
```

### 3. 安装依赖
```powershell
uv pip install -e ".[dev]"
# 或
pip install -e ".[dev]"
```

### 4. 配置环境变量
```powershell
Copy-Item .env.example .env.prod
# 编辑 .env.prod 填入三家 API Key
```

### 5. 启动服务
```powershell
# 启动 LiteLLM + 三 Agent（P1 完成后可用）
docker compose --env-file .env.prod up -d

# 或 P0 阶段先单跑 GLM Agent
docker compose --env-file .env.prod up -d litellm glm-agent
```

### 6. 验证
```powershell
# 检查 LiteLLM 模型路由
curl http://localhost:4000/v1/models

# 检查 GLM Agent Card
curl http://localhost:12001/.well-known/agent.json

# 跑 e2e 测试
pytest tests/test_p1_e2e.py -m e2e
```

---

## 端口规划

| 服务 | 端口 | 说明 |
|---|---|---|
| LiteLLM Proxy | 4000 | 统一 LLM 入口 |
| GLM Agent | 12001 | ADK A2A Server |
| DeepSeek Agent | 12002 | ADK A2A Server |
| MiniMax Agent | 12003 | ADK A2A Server |
| Orchestrator | 12080 | LangGraph 编排入口 + OpenAI 兼容端点 |
| Filesystem MCP | 12101 | workspace 沙盒文件读写 |
| Fetch MCP | 12102 | URL 抓取 |
| Shell MCP | 12103 | 受限命令执行 |
| Langfuse Web | 3000 | 自托管 trace UI |
| Langfuse Postgres | 5432 | 内部 OLTP（仅本机） |
| Langfuse ClickHouse | 8123 | trace OLAP（仅本机） |
| Langfuse MinIO | 9000 | S3 兼容对象存储（仅本机） |
| **Open WebUI**（P5） | 8080 | 用户前端 |

> **12000-12099 段全部归 a2a-prod 使用**，与原 a2a-agents（11000-11099）完全无重叠。

---

## 项目结构

```
a2a-prod/
├── docs/                  # 必读：NORTH_STAR / ARCHITECTURE / CODESTYLE / DECISIONS
├── agents/                # 三 Agent 实现（ADK + a2a-sdk）
│   ├── base_agent.py
│   ├── glm_agent/
│   ├── deepseek_agent/    # P1
│   └── minimax_agent/     # P1
├── host/                  # P2 编排层入口
├── orchestrator/          # P2 LangGraph 编排引擎
│   ├── graph.py
│   ├── state.py
│   ├── classifier.py
│   ├── executor.py
│   ├── aggregator.py
│   ├── a2a_client.py
│   └── openai_compat.py   # P5 OpenAI 兼容层
├── mcp_servers/           # P3 三个 MCP server
├── observability/         # P4 Langfuse 可观测性
├── frontend/              # P5 Open WebUI 集成
│   ├── README.md
│   ├── docker-compose.yml
│   ├── .env.example
│   └── config/
├── infra/
│   ├── docker-compose.yml # P0+P1+P2+P3+P4+P5
│   ├── litellm/config.yaml
│   ├── mcp/               # P3 MCP Dockerfile
│   ├── k8s/               # P6 K8s 部署清单（Kustomize）
│   │   ├── README.md
│   │   ├── kustomization.yaml
│   │   ├── secrets.yaml.example
│   │   ├── 00-namespace.yaml
│   │   ├── 01-rbac.yaml
│   │   ├── 02-configmap.yaml
│   │   ├── 03-pvc.yaml
│   │   ├── 10-litellm.yaml
│   │   ├── 20-agents.yaml       # 3 Agent Deployment + Service + HPA
│   │   ├── 30-orchestrator.yaml
│   │   ├── 40-mcp.yaml
│   │   ├── 50-langfuse.yaml     # 6 组件
│   │   ├── 60-open-webui.yaml
│   │   ├── 70-network-policies.yaml
│   │   └── 80-pdb.yaml
├── tests/
│   ├── test_p1_e2e.py
│   ├── test_p2_e2e.py
│   ├── test_p3_e2e.py
│   ├── test_p4_e2e.py
│   ├── test_p5_e2e.py
│   ├── contract/          # 契约测试
│   │   ├── test_graph.py
│   │   ├── test_openai_compat.py     # P5
│   │   └── test_k8s_manifests.py     # P6
│   └── conftest.py
├── scripts/
│   ├── check_ports.ps1
│   └── check_env.py
├── .env.example
├── pyproject.toml
└── README.md
```

---

## 文档导航

| 文档 | 用途 |
|---|---|
| [docs/NORTH_STAR.md](docs/NORTH_STAR.md) | **先读** — 项目总方向、做与不做 |
| [docs/SPEC.md](docs/SPEC.md) | **项目规范**（协议 / 接口 / 流程 / 基础设施） |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构图、组件职责、演进路线 |
| [docs/CODESTYLE.md](docs/CODESTYLE.md) | 代码风格、命名、目录约定 |
| [docs/DECISIONS.md](docs/DECISIONS.md) | ADR 架构决策记录 |

---

## 与原 a2a-agents 的关系

| 维度 | 原 a2a-agents | a2a-prod |
|---|---|---|
| 路径 | `D:\trae_Dproject3\a2a\` | `D:\trae_Dproject4\a2a-prod\` |
| 定位 | 学习 / 实验 | 生产 |
| 状态 | 冻结（仅维护） | 主开发 |
| 协议 | 自研 proto_compat | 官方 a2a-sdk |
| Agent | 自研 Executor | Google ADK |
| LLM 适配 | 各家直连 | LiteLLM Proxy |
| 端口段 | 11000-11099 | 12000-12099 |

**绝对原则**：两套系统可以同时运行不冲突。

---

## 当前阶段（P0~P6）的边界

### ✅ 已完成
- **P0** 三 Agent 用 ADK 暴露标准 A2A 协议 + LiteLLM 统一三家国产模型
- **P1** 3 Agent 容器化 + e2e 测试
- **P2** LangGraph 编排（4 模式：DIRECT / DECOMPOSE / NEGOTIATION / WORKFLOW）
- **P3** 3 个 MCP server（filesystem / fetch / shell）+ workspace 沙盒
- **P4** Langfuse v3 自托管 + OTEL trace 双接入（Agent / LiteLLM / Orchestrator）
- **P5** Open WebUI 用户前端 + Orchestrator OpenAI 兼容层（`/v1/chat/completions`）
- **P6** Kubernetes 部署清单（Kustomize）：14 个 yaml / 5 HPA / 5 PDB / 8 NetworkPolicy / 完整安全基线

### ❌ 不做（放后续阶段 P6.1+）
- TLS / Ingress / cert-manager
- Sealed Secrets / External Secrets 替换明文 Secret
- Prometheus / Grafana / Loki 监控栈
- ArgoCD / Flux GitOps 部署
- Velero PVC 备份
- 多模态 / tool_calls / 真实 token 计数（OpenAI 兼容层 P5.1）

### 启动方式二选一

#### 方式 A：docker-compose（开发 / 本地）

```powershell
# 启动所有服务（LiteLLM + 3 Agent + Orchestrator + 3 MCP + Langfuse + Open WebUI）
docker compose --env-file .env.prod up -d

# 等 30s 让 Langfuse 完成 init
Start-Sleep -Seconds 30

# 跑全部 e2e 测试
pytest -m e2e
```

浏览器访问：
- Open WebUI：<http://localhost:8080>
- Langfuse Dashboard：<http://localhost:3000>
- Orchestrator Swagger：<http://localhost:12080/docs>

#### 方式 B：Kubernetes（生产 / 多节点）

```bash
# 1. 起本地 k3d 集群
k3d cluster create a2a-prod --agents 2 --servers 1

# 2. 准备 Secret
cp infra/k8s/secrets.yaml.example infra/k8s/secrets.yaml
vim infra/k8s/secrets.yaml  # 替换所有 REPLACE_ME / CHANGE-ME

# 3. 应用全部
kubectl apply -k infra/k8s/

# 4. 等待 ready
kubectl -n a2a-prod wait --for=condition=ready pod -l app.kubernetes.io/part-of=a2a-prod --timeout=600s

# 5. 暴露服务
kubectl -n a2a-prod port-forward svc/orchestrator 12080:12080 &
kubectl -n a2a-prod port-forward svc/open-webui 8080:8080 &
kubectl -n a2a-prod port-forward svc/langfuse-web 3000:3000 &
```

详见 [`infra/k8s/README.md`](infra/k8s/README.md) 与 [`docs/MIGRATION.md §3`](docs/MIGRATION.md)。

---

## License

MIT
