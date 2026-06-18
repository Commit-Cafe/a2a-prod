# NORTH_STAR — a2a-prod 项目总方向

> 本文档是 a2a-prod 的"北极星"。任何架构决策、代码改动、技术选型偏离本文时，
> 必须先更新本文档并在 DECISIONS.md 记录 ADR。
> **当你在写代码时对某个做法产生怀疑，先回来看一眼本文档。**

---

## 1. 项目愿景

构建一个**生产级、可观测、可演进**的多 Agent 协作系统：

- **协议层**：严格遵循 Google A2A Protocol v1.0+ 规范
- **Agent 层**：用 Google ADK 包装 GLM / DeepSeek / MiniMax 三个国产模型
- **模型适配层**：用 LiteLLM 统一为 OpenAI 兼容接口，屏蔽三家差异
- **未来编排层**：LangGraph StateGraph（P2 引入）
- **未来前端**：Open WebUI + OpenAI 兼容 API（P5 引入）

### 一句话定位
> 让三个国产大模型在标准 A2A 协议下可被发现、可流式对话、可被编排，
> 且整个系统任意一层都能被业界主流开源方案替换。

---

## 2. 必做事项（Must Do）

| 维度 | 要求 |
|---|---|
| 协议合规 | 三个 Agent 必须暴露 `/.well-known/agent.json` Agent Card |
| 流式响应 | 必须支持 SSE `message/stream` |
| 隔离性 | 与 `D:\trae_Dproject3\a2a\` 完全互不打扰（端口、venv、容器、数据） |
| 可观测 | 每个请求有唯一 `request_id`，日志带 trace 上下文（P4 接 Langfuse） |
| 可测 | 关键路径必须有 e2e 测试，docker-compose 起来后能一键跑 |
| 配置外置 | 所有 API Key、端口、模型名走 `.env`，代码里不硬编码 |
| 文档同步 | 任何架构变更必须同步更新 ARCHITECTURE.md 和 DECISIONS.md |

---

## 3. 不做事项（Won't Do — 防止范围蔓延）

### 3.1 永远不做
- ❌ **不自己实现 A2A 协议**：用官方 `a2a-sdk`
- ❌ **不自己实现 LLM 适配**：用 LiteLLM
- ❌ **不在主分支硬编码 API Key**：必须走 `.env`
- ❌ **不做与原 a2a-agents 的双向同步**：两套独立演进

### 3.2 当前阶段（P6.1 生产化）不做
- ❌ TLS / Ingress / cert-manager（K8s 层）
- ❌ Sealed Secrets / External Secrets 替换明文 Secret
- ❌ Prometheus / Grafana / Loki 监控栈
- ❌ ArgoCD / Flux GitOps 部署
- ❌ Velero PVC 备份
- ❌ 多模态 / tool_calls / 真实 token 计数（OpenAI 兼容层 P5.1）
- ❌ WORKFLOW 模式接入 OpenAI 兼容层（`/v1/chat/completions`，留 P2.2）
- ❌ 第二个工作流模板 / 动态 workflow 引擎（YAGNI，留 P2.2+ 视需求）

> P2.1 已完成 SDLC 研发协作工作流（DeepSeek→GLM→MiniMax + 反馈回路）。

> **判断标准**：如果某个改动属于上述"不做"范围，即便看起来"顺手做了更好"，
> 也必须先在 DECISIONS.md 提案，等阶段升级时再讨论。

---

## 4. 验收标准（Definition of Done）

每个阶段必须满足以下条件才能算完成。

### P0 基建周
- [ ] `docker compose up` 能起 LiteLLM 容器
- [ ] `curl http://localhost:4000/v1/models` 返回三个模型
- [ ] GLM Agent 容器能起来，`curl http://localhost:12001/.well-known/agent.json` 返回合规 JSON
- [ ] `curl` 向 GLM Agent 发 `message/send` 能收到响应（走 LiteLLM → 真实 GLM API）

### P1 三 Agent 落地
- [ ] 三个 Agent 容器同时运行，端口 12001/12002/12003
- [ ] 三个 Agent Card 全部合规（JSON schema 校验通过）
- [ ] `tests/test_p1_e2e.py` 一键通过
- [ ] README 有完整启动指南

### P2+（后续阶段）
- 见 ARCHITECTURE.md 各阶段章节

---

## 5. 质量底线（Quality Bar）

任何 PR 必须满足：

1. **可读性**：函数 ≤ 50 行，模块 ≤ 300 行，超出必须拆分
2. **自测性**：新增功能必须有 e2e 或单元测试覆盖
3. **可编辑性**：配置走 pydantic-settings，不写死；模块间走依赖注入，不全局单例
4. **类型完整**：所有公共函数有类型注解，启用 `mypy --strict` 或 `pyright`
5. **文档同步**：新增模块必须更新对应 README 章节

---

## 6. 隔离原则（与原 a2a-agents 的边界）

| 维度 | 原 a2a-agents | a2a-prod |
|---|---|---|
| 路径 | `D:\trae_Dproject3\a2a\` | `D:\trae_Dproject4\a2a-prod\` |
| Python venv | 原 venv 不动 | `.venv-prod`（uv 管理） |
| 端口段 | 11000-11099、7860 | 12000-12099、4000、8080、3000 |
| Docker network | `a2a_default`（如存在） | `a2a-prod-net` |
| 镜像标签 | `a2a-*` | `a2a-prod-*` |
| 数据卷 | 原 `opencode-a2a-*.db` | `a2a-prod-data` |
| 环境变量文件 | `.env` | `.env.prod`（独立副本） |

**绝对原则**：两套系统必须能同时运行不冲突。

---

## 7. 失败回退策略

按用户规则 3（不钻牛角尖）：

- 如果 ADK + LiteLLM + GLM 链路连续 3 次跑不通：
  → 退回"直接用 a2a-sdk 手写 Executor"方案，记录到 DECISIONS.md
- 如果某家国产模型 API 在 LiteLLM 下不兼容：
  → 跳过该模型，先用 mock 占位，进入 P1 后再处理
- 任何连续失败的尝试都要在 DECISIONS.md 留下"尝试记录 + 退出条件"

---

## 8. 文档清单（本项目的源代码之外的"源代码"）

| 文档 | 作用 | 维护频率 |
|---|---|---|
| `NORTH_STAR.md` | 你正在看的这份，方向总则 | 架构变更时 |
| `SPEC.md` | **项目规范单一事实源**（协议 / 接口 / 流程 / 基础设施） | 任何 PR 都可能涉及 |
| `ARCHITECTURE.md` | 当前架构图、组件职责、数据流 | 每阶段更新 |
| `DECISIONS.md` | ADR 架构决策记录 | 每个非平凡决策 |
| `CODESTYLE.md` | 代码风格、命名、目录约定 | 新增风格规则时 |
| `README.md` | 启动指南、快速上手 | 每个版本 |

---

**最后更新**：2026-06-17（P2.1 SDLC WORKFLOW 完成实装）
**作者**：a2a-prod 团队
