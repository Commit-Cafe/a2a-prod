# frontend/

> Open WebUI 集成目录（P5 阶段，详见 ADR-0009 / SPEC §3.9）。

## 目录结构

```
frontend/
├── README.md                 # 本文件
├── docker-compose.yml        # P5 独立 compose（Open WebUI 单服务，可与主 compose 并行）
├── .env.example              # Open WebUI 专用环境变量模板
├── config/
│   ├── openai-compat.md      # OpenAI 兼容契约说明
│   ├── system-prompts.md     # 推荐 system prompt 模板
│   └── models.md             # model 名映射表
└── ...
```

## 快速开始

### 选项 A：随主 compose 一起起（推荐）

```powershell
cd D:\trae_Dproject4\a2a-prod
docker compose --env-file .env.prod up -d open-webui
```

启动后浏览器访问 `http://localhost:8080`，首次进入会引导创建管理员账号。

### 选项 B：单独起（解耦主栈）

```powershell
cd D:\trae_Dproject4\a2a-prod\frontend
cp .env.example .env
# 编辑 .env，确认 OPENAI_API_BASE_URL 指向可用的 orchestrator
docker compose up -d
```

## 关键配置

Open WebUI 通过两个环境变量对接编排层：

| 变量 | 含义 | 默认值 |
|---|---|---|
| `OPENAI_API_BASE_URL` | OpenAI 兼容端点（Orchestrator） | `http://orchestrator:12080/v1` |
| `OPENAI_API_KEY` | Bearer Token（与 `LITELLM_MASTER_KEY` 同步） | `${LITELLM_MASTER_KEY}` |

`OPENAI_API_BASE_URL` 在主 compose 中已硬编码为 `http://orchestrator:12080/v1`，
即 Open WebUI 容器内通过 docker 网络名访问 orchestrator。

## 模型选择

Open WebUI 启动后会自动从 `/v1/models` 拉取可用模型列表，预期看到：

- `glm-agent`（GLM-5.1 代码审查 Agent）
- `deepseek-agent`（DeepSeek 需求/方案 Agent）
- `minimax-agent`（MiniMax 代码实现 Agent）
- `auto`（默认；让 Orchestrator 按关键词自动路由）

## 数据卷

- `a2a-prod-open-webui-data`：用户、会话、知识库持久化
  （bind mount 到容器内 `/app/backend/data`）

## 反向代理 / Ingress（生产）

P6 阶段在 K8s 中通过 Ingress 暴露，详见 `infra/k8s/60-open-webui.yaml`。

开发环境直接用 `http://localhost:8080` 即可。
