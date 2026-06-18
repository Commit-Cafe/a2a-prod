# DECISIONS — a2a-prod 架构决策记录（ADR）

> 每个非平凡决策一条 ADR，按时间倒序。
> 决策变更时新增 ADR 标记旧 ADR 为 Superseded，不修改旧 ADR。

---

## ADR-0008: Langfuse 可观测性接入策略（P4 阶段）

**日期**：2026-06-08
**状态**：Accepted
**决策者**：用户 + AI 助手
**关联 SPEC**：§3.8（新增）；§5 变更日志 v0.6.0

### 背景

P4 阶段引入 LLM 可观测性，NORTH_STAR §2 必做事项明确要求：
> 每个请求有唯一 `request_id`，日志带 trace 上下文（P4 接 Langfuse）

ARCHITECTURE §2 P4 进一步列出两件事：
1. Langfuse 自托管（docker-compose 加服务）
2. 各节点 trace 装饰器

调研结论：
1. **Langfuse v3**（2024-12 发布）官方 [docker-compose.yml](https://github.com/langfuse/langfuse/blob/main/docker-compose.yml) 自带 8 个服务：langfuse-web / langfuse-worker / postgres / clickhouse / redis / minio + 2 init。资源要求 4 cores + 16 GiB。
2. **接入协议**：Langfuse v3.22+ 支持 OTLP（OpenTelemetry Protocol），可直接走 OTEL SDK。
3. **ADK 集成**：官方提供 `openinference-instrumentation-google-adk`，一行 `GoogleADKInstrumentor().instrument()` 即可自动捕获 LLM call / tool call。
4. **LiteLLM 集成**：`litellm.callbacks = ["langfuse_otel"]` 一行配置，所有经过 LiteLLM 的请求自动 trace。
5. **关键 Bug**（[GitHub issue #9871](https://github.com/langfuse/langfuse/issues/9871)）：自托管 + ADK 集成 401 错误，根因是 `LANGFUSE_HOST` 用了 `localhost` 而非 docker 网络名。**必须**用 `http://langfuse:3000`（容器内）或显式设 `OTEL_EXPORTER_OTLP_ENDPOINT` + Basic Auth。

### 决策

**P4 阶段接入策略：**

1. **部署方案：v3 完整版（8 服务）**
   - 直接复用官方 `github.com/langfuse/langfuse` 仓库根目录的 `docker-compose.yml`
   - 8 个服务全部起：langfuse-web（3000）、langfuse-worker、postgres、clickhouse、redis、minio + 2 init
   - 命名遵循 §3.2：`a2a-prod-langfuse-*` 容器名

2. **认证方案：INIT 变量自动创建（开发环境）**
   - 通过 `LANGFUSE_INIT_*` 环境变量预置 Organization + Project + 默认 PK/SK（`pk-lf-local` / `sk-lf-local`）
   - **生产环境（P6）必须替换为 UI 手动创建的强凭证**
   - **禁止**在 `.env.prod` 写真实 PK/SK（开发场景默认值够用；P6 再升级）

3. **Trace 接入：ADK + LiteLLM 双接入**
   - **3 Agent 进程**：`GoogleADKInstrumentor().instrument()` 捕获 LlmAgent 的 LLM call / tool call（filesystem/fetch/shell）
   - **LiteLLM 进程**：`litellm.callbacks = ["langfuse_otel"]` 兜底所有经 LiteLLM 的请求（包括 orchestrator 直调）
   - **Orchestrator 进程**：`observability/tracing.py` 提供 `@trace_node` 装饰器（基于 `langfuse` Python SDK 原生 `update_current_span`），装饰 LangGraph 节点函数

4. **OTEL 端点配置（关键 Bug 防御）**
   - Agent 容器内 `LANGFUSE_HOST` MUST = `http://langfuse:3000`（**不是** `localhost`）
   - 显式设 `OTEL_EXPORTER_OTLP_ENDPOINT` + Basic Auth 头，避免 401
   - 写入 `infra/docker-compose.yml` 的 `langfuse-web` 服务的 environment 块

5. **数据契约（SPEC §3.8 新增）**
   - 每个 trace MUST 含 `request_id` / `session_id` / `user_id` 三个 metadata
   - 3 Agent 容器启动时 MUST 调 `get_client().auth_check()` 探活，失败则容器退出非 0
   - Orchestrator 把 request_id 透传到下游 Agent（A2A message/send params.metadata.request_id）

6. **依赖（pyproject.toml 新增）**
   - `langfuse>=3.0.0,<4.0.0`（Python SDK v3）
   - `openinference-instrumentation-google-adk>=0.1.0,<1.0.0`（ADK instrumentor）
   - **禁止**用 `langfuse.openai` 模块（那是 OpenAI 直调的 drop-in，不适用于 LiteLLM 中转场景）

### 备选方案（已否决）

| 方案 | 否决原因 |
|---|---|
| Langfuse v2 轻量版 | 官方 v2 安全更新截至 2025 Q1，已 EOL；且 v2 API 与 v3 不兼容 |
| 不自托管，用 Langfuse Cloud | 需注册第三方账号 + 跨境网络；与"完全自托管"定位冲突 |
| 不接 LiteLLM，只接 ADK | Orchestrator 直调 Agent 的 trace 漏掉；3 Agent 调 MCP tool 的内部 span 也会漏 |
| 用 OpenTelemetry Collector 中转 | 多一层 indirection，价值不大；增加维护成本 |
| 用 Langfuse Cloud 免费版 | 同"不自托管"否决原因 |

### 退路（如果 P4 跑不通）

**退出条件 1**：docker compose 起 Langfuse v3 完整版连续 3 次启动失败（资源不足 / 端口冲突 / 网络问题）→ 退出条件触发：
→ 改用 **v2 轻量版**（1 server + 1 postgres），记录到本 ADR "Superseded" 段落
→ 资源不足场景进一步退路：把 ClickHouse 换成 Postgres-only（牺牲查询性能）

**退出条件 2**：`GoogleADKInstrumentor().instrument()` 在 3 Agent 进程启动时报错（兼容性 / 依赖冲突）→ 退出条件触发：
→ 改用 **纯 LiteLLM 回调**（`litellm.callbacks = ["langfuse_otel"]`）兜底，ADK 侧不接
→ LLM call 仍能 trace，但 MCP tool call 等 ADK 内部 span 漏

**退出条件 3**：自托管 ADK 401 Bug 修复 3 次仍失败（[issue #9871](https://github.com/langfuse/langfuse/issues/9871)）→ 退出条件触发：
→ 显式设 `OTEL_EXPORTER_OTLP_ENDPOINT` + 完整 Basic Auth 头（绕过 SDK 默认行为）
→ 若仍失败：考虑降级到 Langfuse Cloud 免费版（非自托管）

### 关联变更

- SPEC v0.5.0 → v0.6.0：新增 §3.8（Langfuse 集成 spec）+ v0.6.0 changelog
- ARCHITECTURE §2 P4：从 todo 改为 done
- ARCHITECTURE §3 端口表：新增 Langfuse 3000 / 5432 / 8123 / 6379 / 9000（v3 暴露给宿主机的端口）
- 目录结构：新增 `observability/` 目录

---

## ADR-0007: MCP 工具生态接入策略（P3 阶段）

**日期**：2026-06-08
**状态**：Accepted
**决策者**：用户 + AI 助手

### 背景

P3 阶段引入 MCP (Model Context Protocol) 工具生态，让 3 个 Agent 能调用外部工具。
NORTH_STAR §3.2 与 ARCHITECTURE §2 P3 都明确列出三个工具：filesystem / shell / web_fetch。

调研结论：
1. MCP 官方 Python SDK（modelcontextprotocol/python-sdk，22.8k star）活跃维护，`FastMCP` 几行代码即可暴露 tool。
2. ADK 1.34 原生支持 `MCPToolset`，自动把 MCP tools 暴露给 `LlmAgent`。
3. 官方 reference servers（modelcontextprotocol/servers，84.3k star）提供 Fetch（Python）和 Filesystem（TypeScript）现成实现；Shell 无官方版。
4. MCP 传输方式：stdio / SSE / Streamable HTTP（2025-03 推出，下一代标准）。

### 决策

**P3 阶段接入策略：**

1. **传输方式**：全部走 Streamable HTTP（独立容器、独立端口）
   - Filesystem MCP Server：`:12101/mcp`
   - Fetch MCP Server：`:12102/mcp`
   - Shell MCP Server：`:12103/mcp`
   - Healthcheck 端点：`:/healthz`

2. **Server 来源**：
   - **Filesystem**：自研（FastMCP，几十行；按 Agent 角色分读写权限）
   - **Fetch**：复用官方 `mcp-server-fetch` + 自研 Streamable HTTP wrapper
   - **Shell**：自研（FastMCP + 命令 allowlist + 30s timeout）

3. **Filesystem 沙箱**：`a2a-prod/workspace/` 作为唯一暴露根
   - 强制 `os.path.realpath` + 前缀校验，防路径逃逸
   - 默认空目录，测试时放 `samples/` 子目录

4. **Shell allowlist**：`pytest` / `ruff check` / `mypy` / `git status` / `git diff` / `cat` / `ls`
   - subprocess + `timeout=30s`
   - 拒绝任何含 `&&` / `;` / `|` / 反引号 / `$()` 的复合命令

5. **Agent 接入（按人设分配）**：
   - DeepSeek（PM/CTO 方案）：fetch + filesystem（只读）
   - MiniMax（程序员写码）：filesystem（读写）+ shell
   - GLM（代码审查）：filesystem（只读）+ fetch

6. **依赖增量**：
   - `mcp[cli]>=1.5.0,<2.0.0`（核心 SDK）
   - `mcp-server-fetch>=2025.0.0`（官方 fetch 实现）
   - 不引入 Node.js（保持全 Python 技术栈）

### 备选方案

1. **传输方式备选**：
   - **stdio sidecar**：MCP server 作为 Agent 子进程。优点：简单、零网络。缺点：Agent 镜像需装额外依赖；3 Agent 各起一份 MCP server，资源浪费。
   - **混合（Fetch HTTP / Filesystem+Shell stdio）**：折中方案，但增加配置复杂度。

2. **Shell server 备选**：
   - **社区现成的**：GitHub 有 `mcp-server-shell` 类项目，但活跃度低、安全审计不充分。
   - **欣掉 Shell**：缩小范围最安全，但 MiniMax 程序员 Agent 无法跑 pytest/ruff，人设发挥不出来。

3. **Filesystem 范围备选**：
   - **项目根 a2a-prod/（只读）**：覆盖广但风险高（含 `.env.prod` / API key 风险）。
   - **项目根 + workspace/**：复杂度高。

### 理由

- **传输走 Streamable HTTP**：与现有 Docker 多容器架构最匹配，3 个 MCP server 独立伸缩，与 ADK 官方推荐 Pattern 2（Remote MCP Servers）一致。
- **Filesystem / Shell 自研**：核心代码量小（< 200 行/个），完全可控；Filesystem 用 Python，复用官方 TypeScript 版需要 Node.js 依赖。
- **Fetch 复用官方**：成熟稳定、特性丰富（HTML→Markdown 转换），不必重造。
- **沙箱 + allowlist 双保险**：MCP 工具调用 LLM 生成的参数，必须有显式安全边界。
- **按人设分配工具**：避免给每个 Agent 都开全部工具，符合"最小权限原则"。

### 后果

- **正面**：
  - 3 个 Agent 从纯对话升级为可调用工具，能力质变。
  - MCP 标准化接口，未来加新工具只需写新的 MCP server，不改 Agent。
  - 全 Python 栈，无 Node.js 依赖。

- **负面**：
  - 多 3 个容器，资源占用上升（每个 MCP server ~50MB）。
  - Filesystem / Shell 自研需要持续维护（vs 官方 fetch）。

- **风险**：
  - ADK 1.34 的 MCPToolset 对 Streamable HTTP 支持若不稳定 → 退到 SSE 传输（API 几乎一致）。
  - 路径逃逸 / 命令注入漏洞 → contract 测试必须覆盖（含 `../../etc/passwd` / `; rm -rf` 等 payload）。

### 退出条件

- 如果 Streamable HTTP 在 ADK 1.34 下连续 3 次跑不通 → 退到 SSE 传输。
- 如果 `mcp-server-fetch` 官方包与 mcp SDK 主版本不兼容 → 自研 fetch（FastMCP + httpx + html2text，~100 行）。
- 如果 Shell allowlist 出现 1 次安全事件 → 立即下线 Shell MCP server，转人工 review 模式。

---

## ADR-0001: 采用 Google ADK + LiteLLM 双层架构

**日期**：2026-06-05
**状态**：Accepted
**决策者**：用户 + AI 助手

### 背景
a2a-prod 需要让 GLM-5.1 / DeepSeek / MiniMax-M3 三个国产模型通过标准 A2A 协议暴露。

### 决策
- **Agent 框架**：Google ADK（`adk api_server --a2a` 一行暴露 A2A 服务）
- **LLM 适配**：LiteLLM Proxy 统一三家国产模型为 OpenAI 兼容接口
- **协议 SDK**：a2a-sdk（官方 Python 实现）

### 备选方案
1. **a2a-sdk 手写 Executor**：最灵活但样板代码多，需要自己处理 streaming/task 状态机
2. **LangGraph 直接包装**：最简单但 A2A 协议层要自己写
3. **Microsoft Multi-Agent-Accelerator**：偏 Azure 生态，国产模型支持弱

### 理由
- ADK 原生支持 A2A 暴露，零样板
- LiteLLM 已支持 100+ LLM 提供商，国产模型适配成本低
- 官方 SDK 协议合规风险最低

### 后果
- **正面**：开发量下降 ~60%，协议合规性提升
- **负面**：增加 LiteLLM 中间层（多一跳延迟 ~5ms）
- **风险**：ADK 对国产模型支持未充分验证，需 P0 阶段验证

### 退出条件
- 如果 ADK + LiteLLM + GLM 链路连续 3 次跑不通
- 退回 ADR-0001 备选方案 1（a2a-sdk 手写）

---

## ADR-0002: 与原 a2a-agents 完全隔离

**日期**：2026-06-05
**状态**：Accepted
**决策者**：用户

### 背景
原 `D:\trae_Dproject3\a2a\a2a-agents\` 仍在维护中，用户明确要求"完全隔离、互不打扰"。

### 决策
- 物理路径：`D:\trae_Dproject4\a2a-prod\`
- Python venv：`.venv-prod`（uv 管理）
- 端口段：12000-12099（原项目 11000-11099）
- Docker network：`a2a-prod-net`
- 数据卷：`a2a-prod-data`
- 环境变量：`.env.prod`

### 后果
- 两套系统可同时运行不冲突
- 代码完全不共享（必要时复制粘贴，不做软链接或共享包）
- 文档独立

---

## ADR-0003: 首发里程碑选 P0+P1（三 Agent 跑通 A2A）

**日期**：2026-06-05
**状态**：Accepted
**决策者**：用户

### 背景
三个候选首发：
1. P0+P1：协议层 + 三 Agent 跑通
2. P0+P2：单 Agent + LangGraph 编排
3. P0+P5：最快看到完整 WebUI

### 决策
选 1：**P0+P1 三 Agent 跑通 A2A 协议**

### 理由
- 协议层是整个系统的根基，先证明国产三模型能标准 A2A 协作
- 编排层和 UI 层都是上层建筑，根基不稳后面都是空中楼阁
- 最快验证 ADK + LiteLLM + 国产模型的核心假设

### 后果
- P0+P1 完成后系统还无法对最终用户开放（无 UI）
- 但能为 P2-P6 提供坚实的 Agent 基础设施

---

## ADR-0004: 前端选 Open WebUI 而非自研 Vue

**日期**：2026-06-05
**状态**：Accepted
**决策者**：用户

### 背景
原 a2a-agents 用自研 Vue 3 + Flask，完成度有限。

### 决策
a2a-prod 前端用 **Open WebUI**，编排层暴露 OpenAI 兼容 `/v1/chat/completions`。

### 备选方案
1. 自研精简 Vue + FastAPI
2. 两者都要（Open WebUI 主用，自研做 demo）

### 理由
- Open WebUI 已支持多 Agent、流式、插件，UI 完成度远超自研
- 省下 80% 前端工作量，专注编排核心
- OpenAI 兼容协议已是行业事实标准

### 后果
- 失去部分定制空间（如协作空间多轮讨论的 UI）
- P5 阶段补 MIGRATION.md 指引原项目用户迁移

---

## ADR-0006: 采用 SPEC.md 作为项目规范单一事实源

**日期**：2026-06-05
**状态**：Accepted
**决策者**：用户 + AI 助手

### 背景

P0-2 阶段写代码前，发现项目规范散落在多份文档（NORTH_STAR / ARCHITECTURE / CODESTYLE），
导致：
- 接口契约（Agent Card 字段、A2A 方法、错误码映射）无处可写，容易在 PR 里临时拍脑袋
- 基础设施规则（端口、命名、Dockerfile 约束）散在 ARCHITECTURE / README / .env.example
- review 时缺少"权威"标准，难以拒绝"看起来还行"的 PR

### 决策

新增 `docs/SPEC.md`，作为项目规范的**单一事实源**（Single Source of Truth），
覆盖四大画布：

1. A2A 协议层 spec（Agent Card / 方法 / SSE / 错误码 / 超时）
2. Agent 接口 spec（BaseAgent / 配置注入 / 日志 / 异常分层）
3. 基础设施 spec（端口 / docker-compose / Dockerfile / 镜像 tag / 启动顺序）
4. 开发流程 spec（分支 / commit / 质量门 / 版本号 / ADR / 文档同步 / 测试）

文档定位与仲裁顺序：
**NORTH_STAR → SPEC.md → DECISIONS.md → ARCHITECTURE.md → CODESTYLE.md → README.md**。

### 备选方案

1. **散在现有文档**：ARCHITECTURE 加协议章节、CODESTYLE 加流程章节
   - 优点：不新增文件
   - 缺点：协议 / 接口属于"对外契约"而非"代码风格"，放 CODESTYLE 不合适；
     放 ARCHITECTURE 又会被"架构图"主旋律淹没
2. **多文件子目录 docs/spec/**：00-overview / 01-a2a / 02-agent / 03-infra / 04-process
   - 优点：分文件易维护
   - 缺点：P0+P1 阶段内容量不足以支撑拆 5 个文件，反而增加导航成本
3. **机器可校验的 schema**：JSON Schema / OpenAPI
   - 优点：可自动校验
   - 缺点：契约表达力弱（无法描述 SSE 序列、超时策略）

### 理由

- 单文件 Markdown：与项目当前文档体量匹配（< 1000 行可控）
- 用 RFC 2119 关键字（MUST / SHOULD / MAY）：review 时无歧义
- 文档 + 守护点声明：在描述性条款里指出"由 tests/contract/test_xxx.py 守护"，
  P0-5 / P1 阶段补对应测试即可形成闭环
- SPEC.md 自身版本化：在 §5 维护变更日志，便于 PR 引用"spec v0.2.0"

### 后果

- **正面**：P0-4 / P0-5 / P1 写代码有明确接口契约，review 标准统一
- **正面**：未来 P2-P6 阶段新增能力（编排 / 工具 / UI / K8s）有清晰位置可加章节
- **负面**：SPEC.md 与 ARCHITECTURE / CODESTYLE 有部分内容重叠（端口表 / 命名规则），
  需在后续 PR 中将重叠处归一（保留 SPEC，ARCHITECTURE 改为引用）
- **风险**：若团队成员不养成"先看 spec"的习惯，spec 会沦为摆设；
  通过 §4.3 质量门 + PR 模板硬性引导

### 退出条件

- 如果 SPEC.md 长度超过 2000 行：拆分为 `docs/spec/` 子目录
- 如果连续 3 个 PR 因 spec 模糊导致返工：评估是否引入机器可校验 schema
- 如果团队拒绝遵守 SPEC.md 仲裁顺序：回到 ADR-0006 备选方案 1（散在现有文档）

---



**日期**：2026-06-05
**状态**：Accepted
**决策者**：用户 + AI 助手

### 背景

P0-2 阶段核对依赖时发现 `pyproject.toml` 中的版本范围严重过时：

| 包 | 原范围 | PyPI 实际最新 | 问题 |
|---|---|---|---|
| `google-adk[a2a]` | `>=0.1.0,<0.2.0` | 1.34.0（稳定）/ 2.2.0（含 breaking） | 原范围已无可用版本 |
| `a2a-sdk` | `>=0.2.0,<0.3.0` | 1.1.0（v1.0 已 breaking） | 原范围已无可用版本 |

A2A 协议本身在 2026-03-12 发布 v1.0 稳定版，但：
- ADK 1.34 的 `[a2a]` extra 锁定 `a2a-sdk>=0.3.4,<0.4`
- ADK 2.x 有 session schema breaking change，且 2.x 的 a2a extra 尚未升到 v1.0
- a2a-sdk v1.0 引入 protobuf-based 类型系统，迁移成本不低

### 决策

**P0+P1 阶段采用路线 A（保守稳妥）：**

- `google-adk[a2a]>=1.34.0,<1.35.0`（锁定 1.34.x）
- `a2a-sdk>=0.3.4,<0.4.0`（与 ADK 1.34 a2a extra 对齐）
- LiteLLM 用 docker 镜像 `ghcr.io/berriai/litellm:main-stable`（独立版本）

### 备选方案

1. **路线 B — 直接上 v1.0**：ADK 2.x + a2a-sdk 1.x
   - 优点：一步到位用稳定协议
   - 缺点：ADK 2.x breaking change 多、a2a extra 未跟上需手写 Starlette routes
2. **路线 C — 浮动最新**：不锁版本号
   - 优点：自动获得 bugfix
   - 缺点：P0+P1 验收期不可控，违反"质量底线"的可重复构建要求

### 理由

- P0+P1 核心目标是验证"ADK + LiteLLM + 国产模型"链路通畅，**不引入额外迁移风险**
- ADK 1.34 + a2a-sdk 0.3 是 ADK 官方测试矩阵，兼容性最好
- 协议 v1.0 与 v0.3 在 Agent Card / message/send / SSE 三大场景的语义差异不大，
  P2 引入 LangGraph 编排时再统一升级到 v1.0

### 后果

- **正面**：P0-4 / P0-5 / P1 的实现路径清晰，与官方 samples 一致
- **负面**：未来需要一次 v0.3 → v1.0 迁移（已记录在 a2a-sdk 官方迁移指南）
- **风险**：若 v0.3 在 P0+P1 期间被官方废弃，需提前升 v1.0

### 退出条件

- 如果 a2a-sdk v0.3.x 在 PyPI 撤架（极少见）→ 立即升 v1.0
- 如果 P2 LangGraph 编排需要 v1.0 才有的能力（如 signed agent card）→ 升 v1.0
- 如果连续 3 次因 ADK 1.x bug 阻塞 → 评估升 ADK 2.x

---



```markdown
## ADR-XXXX: <决策标题>

**日期**：YYYY-MM-DD
**状态**：Proposed | Accepted | Rejected | Superseded by ADR-YYYY
**决策者**：

### 背景
<为什么需要这个决策>

### 决策
<选了什么>

### 备选方案
<还考虑过什么>

### 理由
<为什么选这个>

### 后果
<正面 / 负面 / 风险>

### 退出条件（可选）
<什么情况下会推翻此决策>
```

---

## ADR-0009: Open WebUI 集成与 OpenAI 兼容层设计

**日期**：2026-06-08
**状态**：Accepted
**决策者**：架构组

### 背景

P5 阶段需要给 a2a-prod 加一个用户前端。现有栈（LiteLLM + 3 Agent + Orchestrator）已有

完整 A2A 协议层和 OpenAI 兼容 LLM 调用（LiteLLM 端），但**对外没有统一的
「chat 入口」**——终端用户无法直接用。

P5 的两个核心需求：

1. 用户能通过浏览器/聊天界面与多 Agent 系统对话
2. 用户能在多个 Agent（GLM / DeepSeek / MiniMax）之间切换

### 决策

采用 **Open WebUI（自托管） + Orchestrator OpenAI 兼容层** 的双组件方案：

- **Open WebUI**：用现成开源前端 `ghcr.io/open-webui/open-webui:main`，
  避免从零自研
- **Orchestrator OpenAI 兼容层**：在 Orchestrator FastAPI 内新增
  `/v1/chat/completions` + `/v1/models` 端点，复用 OpenAI Chat Completions
  公开契约（2024-01 版本）
- **model 字段语义**：`glm-agent` / `deepseek-agent` / `minimax-agent` /
  `auto` 四个值；非 auto 走 DIRECT 强制路由，auto 由 Orchestrator 自主分类
- **鉴权**：Bearer Token 校验（`ORCHESTRATOR_API_KEY` 或 `LITELLM_MASTER_KEY`
  兜底）
- **配置**：Open WebUI 通过 `OPENAI_API_BASE_URL=http://orchestrator:12080/v1`
  走 docker 网络内访问 Orchestrator

### 备选方案

1. **路线 A — 自研前端（Streamlit / Gradio）**：
   - 优点：完全可控
   - 缺点：开发成本高，无会话/知识库/插件生态
2. **路线 B — 单独 LiteLLM 直接挂 WebUI（绕过 Orchestrator）**：
   - 优点：少一层
   - 缺点：失去多 Agent 路由 / DECOMPOSE 编排能力（违背 P2 核心价值）
3. **路线 C — 用 LiteLLM 自带 OpenAI 端点 + 路由规则**：
   - 优点：复用 LiteLLM
   - 缺点：LiteLLM 路由粒度只到 model 名级别，无法做"按 query 分类到不同 Agent"
4. **路线 D — 路线 A+B+C 都不是**（P5 选 D）

### 理由

- 路线 D 把"前端"与"编排"职责清晰分离：
  - WebUI 只管 UI（会话/插件/知识库）
  - Orchestrator 只管业务（路由 / 编排 / 鉴权）
- 复用 Open WebUI 的开源生态：
  - 免费拿到会话管理、知识库（RAG）、插件、用户/权限、移动端适配
- OpenAI 兼容层让 Orchestrator 未来可对接**任何** OpenAI 客户端
  （Open WebUI / Cherry Studio / LobeChat / Continue 等），不绑定前端
- model 字段的"强制 DIRECT"语义让用户可绕过自动分类
  （与 Open WebUI 多 Model 切换的 UX 对齐）

### 后果

- **正面**：
  - P5 工作量大幅降低（不需要自研 UI）
  - 多端复用同一套 OpenAI 协议
  - 为 P5.1（多模态 / tool_calls）留出升级路径
- **负面**：
  - 依赖 Open WebUI 上游（如 fork 升级慢需自维护）
  - OpenAI 兼容层是**部分**实现（usage / tool_calls 等 P5.1 补）
  - WebUI 与 Orchestrator 多了一层网络依赖（`a2a-prod-net` 故障会同时挂）
- **风险**：
  - Open WebUI 上游 breaking change 频率较高（>1 次/季度）
  - LiteLLM master key 一旦泄露 = Open WebUI / Orchestrator 全部可写

### 退出条件

- 如果 Open WebUI 长期不更新 / 闭源 → 评估 LobeChat / 自研 Streamlit
- 如果 OpenAI 兼容层演进撞上 LiteLLM 的能力天花板 → 评估在 WebUI ↔ Orchestrator
  之间塞 LiteLLM 做协议转换
- 如果 `model=glm-agent` 强制路由被用户滥用（如误以为能改 model 参数）→ 引入
  P5.1 的"模型注册表"模式

---

## ADR-0010: Kubernetes 部署方案选型

**日期**：2026-06-08
**状态**：Accepted
**决策者**：架构组

### 背景

P0~P5 阶段都用 docker-compose 单机部署。生产化时面临：

1. **单机不可用**：单机故障 = 全栈停服
2. **水平扩缩容**：不能根据流量自动加机器
3. **滚动升级**：手工 `docker compose up` 容易掉线
4. **多环境隔离**：dev / staging / prod 共用一台机器

P6 需要一套可在多云部署的方案。

### 决策

采用 **Kustomize（裸 K8s manifests，不引入 Helm）** 作为 K8s 部署工具：

- 14 个 yaml 文件，按"组件拓扑"分文件
- Kustomize 作为组合器（替代 Helm）
- 默认装 k3d（本地）/ EKS-AKS-GKE（云）
- 关键安全基线：default-deny NetworkPolicy + runAsNonRoot + readOnlyRootFilesystem
- 5 个 HPA + 5 个 PDB 保障关键服务弹性

### 备选方案

1. **路线 A — 纯 Helm chart（最常用）**：
   - 优点：生态成熟，组件可参数化
   - 缺点：模板语言复杂（Go template + values.yaml 嵌套），学习曲线陡；
     对小项目"杀鸡用牛刀"
2. **路线 B — Kustomize（裸 manifest）**：
   - 优点：纯 YAML，可读性高；与裸 kubectl apply 等价；适合 K8s 原生理解
   - 缺点：模板复用能力弱（只能 patch，不能像 Helm 那样动态生成 value）
3. **路线 C — ArgoCD ApplicationSet + Kustomize**：
   - 优点：自动多环境同步
   - 缺点：依赖 ArgoCD 部署（先有鸡先有蛋问题）
4. **路线 D — KubeVela / Open Application Model**：
   - 优点：抽象层次更高
   - 缺点：1.0 之前生态不稳定

P6 选 B（Kustomize）；路线 C 待 P6.1 引入（依赖 ArgoCD 先落地）。

### 理由

- 路线 B 把"配置"和"逻辑"完全解耦：
  - 任何 K8s 工程师打开 yaml 都能看懂
  - 后续切到 Helm / ArgoCD 时只换工具，yaml 不动
- 避免 Helm 模板的隐性 bug（Go template 错误只在运行时发现）
- 路由：default-deny-all + 显式 allow 是 K8s 网络隔离最佳实践
- 三探针（startup + liveness + readiness）能解决"启动慢"与"运行时死"两类故障

### 后果

- **正面**：
  - 可在本地（k3d） / 云（EKS-AKS-GKE）用同一份 yaml
  - 滚动升级 + 自动扩缩容 + 故障自愈全自动
  - NetworkPolicy + 安全基线满足大部分合规要求
- **负面**：
  - 14 个 yaml 文件维护成本（每个新组件都要改多个文件）
  - Kustomize 不能像 Helm 那样用 values.yaml 一键换镜像 tag（需手工改 kustomization.yaml）
  - 当前 Secret 用 K8s Secret 明文（base64）—— 仅适合开发/测试，
    生产必须上 Sealed Secrets / External Secrets（已记录在 MIGRATION.md §4.1）
- **风险**：
  - K8s 版本兼容性（用 1.28+ API，1.24 之前 NetworkPolicy/HPA v2 行为不同）
  - 多个 HPA 误配可能导致节点资源耗尽
  - Langfuse 6 组件均为单实例，PVC 故障 = 不可恢复（需 Velero 备份）

### 退出条件

- 如果项目扩展到 20+ 组件 → 评估 Helm / KubeVela
- 如果需要 GitOps 自动同步 → 引入 ArgoCD
- 如果 Langfuse 改用 SaaS → 简化 K8s 清单（删 langfuse 6 组件）
- 如果企业合规要求 Sealed Secrets → 替换 K8s Secret（已记录 MIGRATION §4.1）

---

**最后更新**：2026-06-08（新增 ADR-0010 K8s 部署方案）

---

## ADR-0011: 实装 SDLC WORKFLOW 编排模式（P2.1）

**日期**：2026-06-17
**状态**：Accepted
**决策者**：架构组

### 背景

P2 已实装 DIRECT + DECOMPOSITION 两种编排模式。用户需要让三家国产模型按
"产品→架构→实现→反馈"顺序协作产出工程产物（端到端研发流程），对应 `state.py`
里被注释的 WORKFLOW 占位（标注 P2.1+）。

### 决策

1. 采用**静态 StateGraph 子图**（方案 A）而非动态 workflow 引擎（方案 B）或手写协程（方案 C）。
   理由：与现有 `graph.py` 风格一致 + 复用 trace/checkpoint + 不引入新概念（YAGNI）。
2. 反馈回路用**单轮上限 N=2**（`MAX_FEEDBACK_ROUNDS`），不做多轮无限循环。
   理由：状态可预测 + e2e 可测 + 避免 LLM 死循环。
3. 节点返回完整 merge 后的值（不引入自定义 LangGraph reducer）。
   理由：与现有 `agent_responses` / `errors` 处理方式一致。
4. **双轨落盘**：spec.md / tech-design.md 由 orchestrator 直接写本地；code/* 由 MiniMax
   通过 filesystem MCP 自己写。理由：与"程序员自己写代码"角色一致。
5. **三层防御**保证角色边界：prompt 硬约束 + 节点兜底校验（剥离超长代码块 / 检测自由发挥）
   + Agent instruction 第二道防线（后置）。
6. **MiniMax 严格遵循上游文档**（DeepSeek Spec + GLM 技术规范），禁止自由发挥；遇阻
   `[NEED_HELP]` 反馈 GLM，不自行解决；GLM 严禁写实现代码，只下编码指令。
7. classifier 关键词优先级：**DECOMPOSITION > WORKFLOW > DIRECT**。
   WORKFLOW 用"硬关键词 + 强正则 + 弱正则/关键词(≥15字)"三层判定，避免短句误判。

### 后果

- **正面**：
  - 三家模型按研发流程顺序协作，产出可落盘的 spec/tech-design/code 完整产物
  - 角色边界清晰（产品/架构/实现），三层防御保证 LLM 不越界
  - 反馈回路让 MiniMax 遇阻时能获得 GLM 指导，不硬解
  - 9 组契约测试（无 docker 秒级）+ 3 e2e 测试覆盖核心路径
- **负面**：
  - 一次完整工作流耗时 1-10 分钟（5+ 次 LLM 调用）
  - classifier 关键词优先级变化导致现有 contract 测试需同步更新（已完成）
  - 节点兜底校验基于启发式（行数 / 关键词），可能有误报/漏报
- **风险**：
  - LLM 不遵守 prompt 硬约束（三层防御缓解，但不能完全消除）
  - MiniMax 不通过 MCP 真落盘（`[FILES_WRITTEN]` 标记 + 目录扫描兜底）

### 退出条件

- 如果 LangGraph 子图共享 state 不兼容 → 把节点直接铺到主图（不抽子图）
- 如果 LLM 角色约束完全失效 → 在节点级做强后处理（regex 剥离 / 重写）
- 如果需要第二个工作流模板 → 评估升级为动态 workflow 引擎（方案 B）

---

**最后更新**：2026-06-17（新增 ADR-0011 SDLC WORKFLOW 编排模式）
