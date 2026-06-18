# a2a-prod Code Review #1（GLM → MiniMax 修复）

> 审查人：GLM-5.2（技术总监 / 代码审查）
> 审查日期：2026-06-18
> 审查范围：a2a-prod 全量源码（`agents/` · `orchestrator/` · `mcp_servers/` · `observability/` · `infra/` · `tests/`），110 文件
> 交付物：本报告 → 交由 **MiniMax-M3** 逐条修复
> 约定：🔴 Blocker（必须修，阻塞上线）/ 🟡 Suggestion（应修）/ 💭 Nit（可选）

---

## 0. 总体评价

整体架构清晰、分层规范（A2A 协议层 / ADK Agent 层 / LangGraph 编排层 / MCP 工具层 / Langfuse 可观测层），文档与代码的对应度高，契约测试覆盖面广。SDLC 工作流的三层防御（prompt / 节点兜底校验 / 角色边界）设计很扎实，是项目亮点。

但存在 **2 个 🔴 部署级 Blocker**（K8s 下 Agent / MCP 端口根本起不来）和 **1 个 🔴 产品级 Blocker**（OpenAI 兼容层"强制指定 Agent"功能实际未生效），外加若干 🟡 一致性 / 测试缺口问题。**未达到 pass 标准，需 MiniMax 修复后回归。**

修复优先级：**B1 → B2 → B3 → S* → N***。

---

## 🔴 Blockers（必须修复）

### B1. K8s Agent 端口环境变量无法映射到 pydantic 配置，Agent 将监听 8000 而非 12001/12002/12003

**位置**：
- `agents/base_agent.py:55-98`（`BaseAgentSettings` 无 `env_prefix`，字段名是 `port` / `host`）
- `infra/k8s/20-agents.yaml:72-75`（注入 `AGENT_NAME` / `AGENT_PORT`）

**问题（Why）**：
`BaseAgentSettings` 的 `model_config = SettingsConfigDict(env_file=".env.prod", extra="ignore", case_sensitive=False)` 没有 `env_prefix`。pydantic-settings 按字段名匹配环境变量，即它只会读 `PORT` / `HOST`，**不会**读 `AGENT_PORT` / `AGENT_NAME`（而且 `name` 是 property 不是字段，`AGENT_NAME` 本就无处可去）。

后果链：
1. K8s 注入 `AGENT_PORT=12001`，但 pydantic 忽略它 → `settings.port` 取默认值 `8000`。
2. Agent 实际监听 **8000**。
3. K8s `containerPort: 12001`、startup/liveness/readiness probe（`port: http` → 12001）、Service `targetPort: http` → 12001 全部打空，**Pod 永远 not ready**。
4. `70-network-policies.yaml` 也只放行 12001/12002/12003，8000 被 NetworkPolicy 拒绝。

> 注意：`infra/docker-compose.yml` 里给 agent 传的是 `PORT: 8000`（line 99/140/181），所以 **docker-compose 路径能跑，只有 K8s 路径挂**。这是 K8s 清单独有的 bug。

**建议修复（二选一，推荐 A）**：
- **A（改 K8s 清单，最小改动）**：把 `20-agents.yaml` 三个 Deployment 的 `AGENT_PORT` 改为 `PORT`、`AGENT_NAME` 删除（name 是类属性硬编码，本就不该由 env 注入），`containerPort` / probe `port` / Service `targetPort` 保持 12001/12002/12003，并确保 `PORT` 环境变量值与之一致。
- **B（改配置层）**：给 `BaseAgentSettings` 加 `env_prefix="AGENT_"`（注意会同时影响 `LITELLM_BASE_URL` 等已有 env，需要同步改 compose + k8s 所有 env 名，风险更大，不推荐）。

---

### B2. K8s MCP 端口环境变量名不匹配，且 `/workspace` 只读无挂载，MCP 跑不起来

**位置**：
- `mcp_servers/filesystem/server.py:31-33` 读 `MCP_FILESYSTEM_HOST` / `MCP_FILESYSTEM_PORT`
- `mcp_servers/fetch/server.py:31-33` 读 `MCP_FETCH_HOST` / `MCP_FETCH_PORT`
- `mcp_servers/shell/server.py:48-50` 读 `MCP_SHELL_HOST` / `MCP_SHELL_PORT`
- `infra/k8s/40-mcp.yaml:62-63` / `153-154` / `245-246` 注入的是 `MCP_PORT`（不存在于代码）
- `infra/k8s/40-mcp.yaml:60-61` / `73-80` `WORKSPACE_ROOT=/workspace` + `readOnlyRootFilesystem: true`，但**没有挂载任何 volume 到 `/workspace`**

**问题（Why）**：
1. **端口名不匹配**：代码读 `MCP_FILESYSTEM_PORT`，K8s 给 `MCP_PORT` → 走代码默认值 12101/12102/12103，**恰好**与 `containerPort` 一致，所以"巧合能起"。但这是隐患——一旦有人改默认值或加 `MCP_PORT` 读取逻辑，行为会静默改变。且 `MCP_FILESYSTEM_HOST` 等 host 变量同样缺失。
2. **`/workspace` 不可写（更严重）**：`readOnlyRootFilesystem: true` 让容器根只读，`WORKSPACE_ROOT=/workspace` 又没有挂载 PVC / emptyDir。后果：
   - `write_file` / `create_directory` 调 `target.parent.mkdir(...)` / `target.write_text(...)` → `OSError: Read-only file system`。
   - `run_command`（shell MCP）在 `/workspace` 跑 pytest，MiniMax 在 SDLC 工作流里落盘的代码也写不进去。
   - `03-pvc.yaml:74-91` 里的 `a2a-prod-workspace` PVC 被注释掉且没有任何 Deployment 引用它。

**建议修复**：
- 把三个 MCP Deployment 的 `MCP_PORT` 改为对应的 `MCP_FILESYSTEM_PORT` / `MCP_FETCH_PORT` / `MCP_SHELL_PORT`（fetch 用不到 workspace，但端口名要对齐），并补 `MCP_FILESYSTEM_HOST` / `MCP_FETCH_HOST` / `MCP_SHELL_HOST`（值 `0.0.0.0`）。
- 取消注释 `03-pvc.yaml` 的 workspace PVC（或新建 emptyDir），在 `mcp-filesystem` 和 `mcp-shell` 的 Deployment 里 `volumeMounts: [{name: workspace, mountPath: /workspace}]` + `volumes: [{name: workspace, persistentVolumeClaim: {claimName: a2a-prod-workspace}}]`。filesystem 需 RW，shell 可 RO（与 docker-compose `:ro` 一致）。
- 注意：`mcp-filesystem` Deployment `spec.replicas: 1`（`40-mcp.yaml:17`）+ PVC 意味着 RWX 需求，PVC `accessModes` 要写 `ReadWriteMany` 或保持单副本（注释里已提到"简化为单实例"，保持单副本即可，PVC 用 RWO）。

---

### B3. OpenAI 兼容层"强制指定 Agent"未生效（`model=glm-agent` 等被静默忽略）

**位置**：
- `orchestrator/__main__.py:299`（计算 `target_agent`）
- `orchestrator/__main__.py:310-315`（调 `orchestrate()` 时**没有传** `target_agent`）
- `orchestrator/graph.py:96-101`（`orchestrate()` 签名**不接收** `target_agent`）
- `orchestrator/openai_compat.py:263-291`（`resolve_target_agent` 文档承诺"强制 DIRECT 路由到该 Agent"）

**问题（Why）**：
SPEC §3.9.3 / 函数 docstring 明确说：传 `model=glm-agent` / `deepseek-agent` / `minimax-agent` → **强制 DIRECT 路由到该 Agent**，绕过 classifier。但实际：

```python
# __main__.py:299
target_agent = openai_compat.resolve_target_agent(effective_model)  # 算出来了，比如 "glm-agent"
# __main__.py:310-315
state = await orchestrate(user_query, session_id=None, user_id=None)  # ← target_agent 被丢弃
```

`orchestrate()` 不接 `target_agent`，内部跑 `classify` 节点重新关键词路由。所以用户在 Open WebUI 选 `glm-agent` 问"重构这段代码"，实际会被 classifier 路由到 **minimax-agent**（命中"重构/代码"关键词），与用户选择相反。`response_model` 字段（`__main__.py:324`）虽然回填了 `glm-agent`，但**实际跑的 Agent 是 MiniMax**，响应内容与 model 字段不符——这是功能正确性 bug，且很难被用户察觉。

流式分支（`_stream_chat_completions`，`__main__.py:339-449`）同样丢弃 `target_agent`。

**建议修复**：
1. 给 `orchestrate()` 加可选参数 `target_agent: str | None = None`；当非空时，跳过 `classify` 节点的关键词路由，直接把 `mode=direct` + `target_agent` 注入 state（或加一个 `force_target_agent` 字段让 `classify` 识别并短路）。
2. `__main__.py` 同步分支和流式分支都把 `target_agent` 透传进 `orchestrate()`。
3. 补契约测试（见 S6）。

---

## 🟡 Suggestions（应修复）

### S1. 模型名 `glm-5` 与 README/SPEC/compose 的 `glm-5.1` 不一致，易误导

**位置**：
- `agents/glm_agent/agent.py:34` `glm_model: str = "glm-5"`，`:60` `return "glm-5"`
- `infra/litellm/config.yaml:12-20` `model_name: glm-5` → `model: openai/glm-5`
- 但 `README.md:7`、`agents/glm_agent/agent.py:38-39` docstring 写 "GLM-5.1"，`infra/docker-compose.yml:102` `GLM_MODEL: ${GLM_MODEL:-glm-5.1}`

**问题**：litellm config 的 `model_name: glm-5` 与 Agent `model_name="glm-5"` 一致（路由能通，**功能 OK**），但 README/docstring/compose env 写的是 `glm-5.1`。这里 `glm-5.1` 只是记录用 env（`glm_model` 字段没被 `model_name` 使用），所以不影响路由，但三处文档/配置名字不统一，后续维护者容易改错。

**建议**：统一口径——要么全改 `glm-5`（与代码事实一致），要么把 litellm config 和 agent `model_name` 都改成 `glm-5.1`（如果实际模型名是 5.1）。任选一种，但要全局一致。

---

### S2. Orchestrator 默认 Agent URL 用 `a2a-prod-glm-agent:8000`，K8s 下与 B1 冲突

**位置**：`orchestrator/executor.py:36-44`

**问题**：
```python
_DEFAULT_AGENT_URLS = {
    "glm-agent": "... or "http://a2a-prod-glm-agent:8000",
    ...
}
```
K8s Service 名（`20-agents.yaml` 的 Service metadata.name）确实是 `glm-agent` / `deepseek-agent` / `minimax-agent`，**不是** `a2a-prod-glm-agent`。docker-compose 的 service 名才是 `glm-agent`（compose 里 service 名是 `glm-agent`，容器名是 `a2a-prod-glm-agent`）。所以这组默认 URL：
- docker-compose 下：service 名 `glm-agent` 才对，`a2a-prod-glm-agent` 是容器名也能解析（docker 网络里容器名可解析）→ 勉强能通，端口 8000 也对（compose 里 agent 监听 8000）。
- K8s 下：Service 名是 `glm-agent`，`a2a-prod-glm-agent` 不存在 → DNS 解析失败。且端口应是 12001（修了 B1 后）而非 8000。

**建议**：默认 URL 不应在代码里硬编码部署形态。改为：
- 默认值留空或用 `AGENT_URLS_GLM_AGENT` 等 env **强制要求注入**（compose 和 k8s 各自注入正确的 service:port）。
- 或至少把默认值改成同时适配两种形态的中性值，并在文档里写清楚 `AGENT_URLS_*` 是必填项。

> 这个问题在修 B1 时一并处理最划算。

---

### S3. `_stream_chat_completions` 是"假流式"（先跑完再切片），且无 cancel/客户端断开处理

**位置**：`orchestrator/__main__.py:339-449`

**问题（Why）**：
代码注释自己也写了"P5.1 升级到真正 incremental"。当前实现：先 `await orchestrate(...)` 跑完整张图（可能 30-90s），拿到 `final_answer` 后再按 20 字符一段 yield。用户体验上"流式"完全没有首字延迟优势，且：
- 客户端中途断开时，`orchestrate()` 已经在跑、无法取消（没有把 request 传入做取消传播）。
- 长任务期间 Open WebUI 会因为长时间无 chunk 触发超时（取决于客户端 read timeout）。

**建议**：
- 短期（P5.1 之前）：在 `event_source()` 开头先 yield 一个 keep-alive 注释 chunk（如 `: keepalive\n\n`），避免客户端超时；并把 `chunk_size=20` 调大或按句切，减少 SSE 包数。
- 中期：接 LangGraph 的 `astream_events` 做真增量（SPEC §3.9.4 已规划）。

---

### S4. `verify_api_key` 用 `provided != expected` 做明文比较，存在时序侧信道

**位置**：`orchestrator/__main__.py:130` `if provided != expected:`

**问题（Why）**：Python `str.__eq__` 短路比较，理论上可通过响应耗时差异逐字节爆破 API key。本地/内网风险低，但 SPEC §3.9 / ADR-0009 把这层定位为生产鉴权，应使用常数时间比较。

**建议**：
```python
import hmac
if not hmac.compare_digest(provided.encode(), expected.encode()):
    raise HTTPException(401, ...)
```

---

### S5. `a2a_client.message_send` 的 `request_id` 默认固定为 1，多并发会重复

**位置**：`orchestrator/a2a_client.py:91` `request_id: int = 1`

**问题（Why）**：DECOMPOSITION 模式 `asyncio.gather` 并行调三个 Agent，三个请求的 JSON-RPC `id` 都是 1。虽然 a2a-sdk 服务端目前不按 id 做去重（每个请求独立 HTTP），但 JSON-RPC 规范要求 id 唯一，未来若接入按 id 复用响应的中间件会出问题。

**建议**：默认用 `uuid` 或自增；`request_id` 参数保留给 e2e 测试显式指定。

---

### S6. 测试缺口：OpenAI 兼容层 `/v1/chat/completions` 无 HTTP 级测试，B3 这类 bug 无法被测试发现

**位置**：`tests/contract/test_openai_compat.py`（全是模块函数级单测，没有 `TestClient` 打 `/v1/chat/completions`）

**问题**：
- `resolve_target_agent("glm-agent") == "glm-agent"` 只验了映射表，没验端到端"传 model=glm-agent → 真的绕过 classifier → 真的调到 glm-agent"。
- 流式分支（`_stream_chat_completions`）零覆盖。
- `verify_api_key` 零覆盖（无 401 测试）。

**建议**：用 `fastapi.testclient.TestClient` 加：
1. `test_chat_completions_forces_agent`：mock `orchestrate`，传 `model=minimax-agent`，断言 `orchestrate` 收到 `target_agent="minimax-agent"` 且 classifier 没跑。
2. `test_chat_completions_stream`：`stream=True`，断言 SSE 有 `chat.completion.chunk` + `data: [DONE]`。
3. `test_chat_completions_auth`：配 `ORCHESTRATOR_API_KEY` 后，无/错 Bearer 返回 401。

> 这条与 B3 配套：先修 B3，再补这组测试防止回归。

---

### S7. SDLC 工作流 `MAX_FEEDBACK_ROUNDS` 读 env 在模块导入时，测试间不可隔离

**位置**：`orchestrator/sdlc_workflow.py:34` `MAX_FEEDBACK_ROUNDS: int = int(os.getenv("SDLC_MAX_FEEDBACK_ROUNDS", "2"))`

**问题**：模块级常量在 import 时冻结，`monkeypatch.setenv("SDLC_MAX_FEEDBACK_ROUNDS", "1")` 后不会生效（除非重新 import）。`check_blocked`（`:316`）和 `_aggregate_workflow`（`aggregator.py:153`）都直接引用这个模块级 int。

**建议**：把读取延迟到函数内，或封装成 `get_max_feedback_rounds()` 函数，测试可 patch。

---

### S8. `extract_user_query` 无视 assistant 历史，多轮对话上下文丢失

**位置**：`orchestrator/openai_compat.py:240-260`

**问题**：只取所有 `system` + 最后一条 `user`，**丢弃所有 assistant 消息**。Open WebUI 多轮对话会把完整历史发过来，当前实现等于每轮都"失忆"。SPEC §3.9.3 没明确要求多轮，但这是用户预期行为。

**建议**：至少把 assistant 历史也拼进 query（或加注释明确"P5 单轮，多轮留 P5.1"）。当前文档注释没说明这点，会误导维护者。

---

### S9. Langfuse v3 迁移未完成，`start_trace` / `trace_node` 在 v3 下走降级分支

**位置**：`observability/tracing.py:102-108`（`if not hasattr(client, "trace"): return {}`）

**问题**：注释自己写了 "langfuse v3 兼容：v3 移除了 client.trace()"。而 `pyproject.toml:52` 锁的是 `langfuse>=3.0.0,<4.0.0`，**生产用的就是 v3**。意味着 `start_trace` 在生产里永远返回 `{}`，`trace_node` 永远走 `start_as_current_observation` 独立 span 分支（`:166`），**trace 层级关系（parent/child）实际没建立**，P4 的核心价值"一条请求一条 trace 下挂多个 span"打折。

ADR-0012 标注"待补"。建议要么尽快补 v3 迁移，要么在文档里明确"P4 当前只产出扁平 span，层级留 ADR-0012"。

---

### S10. `litellm_entrypoint.main` 把 `result` 当 int 返回，但 `run_server` 可能不返回

**位置**：`observability/litellm_entrypoint.py:59-64`

**问题**：`run_server(...)` 在不同 litellm 版本里可能返回 `None` 或直接 `sys.exit`。`return int(result) if isinstance(result, int) else 0` 容错还行，但如果 `run_server` 抛异常，这里没有 try/except，会以非 0 退出且日志只有 traceback（没有"LiteLLM 启动失败"上下文）。

**建议**：包一层 try/except，失败时 `logger.error("litellm_start_failed: ...")` 再 `raise`，便于排障。

---

## 💭 Nits（可选）

### N1. `classifier.py` 关键词长度优先匹配，但同长度时 DeepSeek > MiniMax > GLM 的优先级对英文大小写不健壮
`classifier.py:396-400`：`kw.lower() in query_lower`，但 `all_pairs.sort` 按 `len(kw)` 排序时用的是**原始 kw 长度**（含大写），英文大小写不影响长度所以没问题；只是可读性上 `(-len(x[0]), x[2])` 这行可以加注释说明"长度按原始 kw 算"。纯属 nit。

### N2. `aggregator._aggregate_workflow` 的 emoji 判断有副作用风险
`aggregator.py:138` `status_emoji = "✅" if rounds == 0 or NEED_HELP_MARKER not in impl else "⚠️"`：当 `rounds > 0` 且最终实现里**残留** `[NEED_HELP]` 文本（比如 MiniMax 在说明里引用了这个标记字符串）会误判成 ⚠️。建议改成只看 `workflow_status` 而不是重新扫文本。

### N3. `tests/conftest.py` 探活用 `httpx.get` 同步阻塞，且对每个 e2e marker 都重新探活，启动慢
可接受，但 P5 之后 e2e 探活串行 5 轮，CI 上会多花几秒。可改成并发探活。

### N4. `infra/docker-compose.yml` 的 `mcp-shell` 挂 `../workspace:/app/workspace:ro`，但 SDLC 工作流里 MiniMax 通过 filesystem MCP 写代码后，shell MCP（只读）跑 pytest 能读到——OK；不过 `run_command` 的 `cwd` 校验 `_resolve_cwd` 要求目录存在且可进，只读不影响。无 bug，仅记录。

### N5. `orchestrator/graph.py:80` 的模块级单例 `_COMPILED_GRAPH` 在测试里需手动重置
已有 `build_graph()` 可重建，但 `get_compiled_graph` 的惰性单例若被某测试触发后，后续测试拿到的是旧实例。建议测试 fixture 里 reset。

### N6. `infra/k8s/kustomization.yaml:52` annotation 暴露了 Windows 本地路径 `D:/trae_Dproject4/a2a-prod`
部署到集群后会出现在资源 annotation 里，泄露开发机路径。建议改为相对值或移除。

### N7. `infra/k8s/50-langfuse.yaml` Redis 探活 `redis-cli -a $(REDIS_PASSWORD)` 把密码放命令行
会出现在 `ps` / probe 日志里。可用 `REDISCLI_AUTH` env 注入。minor。

---

## 回归检查清单（MiniMax 修复后请逐项确认）

- [ ] **B1**：`kubectl -n a2a-prod apply -k infra/k8s/` 后，三个 Agent Pod ready；`kubectl exec` 进容器 `netstat` 确认监听 12001/12002/12003；Service `targetPort` 能通。
- [ ] **B2**：MCP filesystem/shell Pod ready；`write_file` / `run_command`（pytest）在 SDLC 工作流里能真正落盘 + 执行；`MCP_PORT` env 名已对齐。
- [ ] **B3**：用 Open WebUI 或 curl 选 `model=glm-agent` 发"重构这段代码"，确认响应来自 GLM 而非 MiniMax；新增的契约测试 `test_chat_completions_forces_agent` 通过。
- [ ] **S1**：全仓 grep `glm-5` / `glm-5.1`，口径统一。
- [ ] **S2**：compose 与 k8s 各自注入正确的 `AGENT_URLS_*`，Orchestrator 能连上三 Agent。
- [ ] **S6**：`pytest tests/contract/test_openai_compat.py` 新增的 HTTP 级测试全绿。
- [ ] **S4/S5/S7**：对应单测通过。
- [ ] 全量回归：`pytest -m "not e2e"`（契约测试）全绿；`ruff check .` / `mypy .` 无新增告警。

---

## 结语

项目骨架质量很高，三层防御 / 契约测试 / 文档同步都做得超出 P6 阶段平均水平。三个 🔴 Blocker 都是"集成层"问题（K8s 清单 ↔ 代码配置 ↔ 端到端契约），单看每个模块都自洽，拼起来才暴露——这正是 Code Review 的价值所在。建议 MiniMax 优先把 B1/B2/B3 修掉并补上 S6 的测试，然后我再做一轮 review，确认全 pass 后即可完结。

— GLM-5.2
