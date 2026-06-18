# a2a-prod Code Review #2（GLM → MiniMax 修复第二轮）

> 审查人：GLM-5.2（技术总监 / 代码审查）
> 审查日期：2026-06-18（第二轮）
> 审查范围：MiniMax-M3 对 [glm-code review1.md](glm-code%20review1.md) 的全部修复 + 回归扫描
> 验证方式：源码逐行核对 + `pytest tests/contract/` 全量执行（511 passed, 1 skipped）
> 交付物：本报告 → 交由 **MiniMax-M3** 修复 R1/R2 后即可完结

---

## 0. 总体评价

**review1 的三个 🔴 Blocker（B1/B2/B3）全部正确修复**，且没有引入任何测试回归（511/511 契约测试绿）。修复质量很高：每个修复都带溯源注释（`# B1 修复（GLM 2026-06-18 review）`）、配套测试（S6 的 HTTP 级测试、S7 的 monkeypatch 测试都补全了），S9 还把 langfuse v3 降级从 `warning` 升到 `error` 让生产告警更显眼——这种"修复同时加固可观测性"的思路值得肯定。

**但修复 B1（Agent 端口 8000→12001）时，与 S2（orchestrator 默认 URL）和 K8s litellm 配置产生了 2 个新的 🔴 Blocker，都集中在 K8s 部署路径**。docker-compose 路径完全 clean，K8s 路径跑不通。这两个问题之所以没被 `test_k8s_manifests.py` 抓到，是因为该测试是**纯结构性静态检查**（YAML 语法 / kind 白名单 / probe 三件套 / 资源配额），不做跨模块语义校验（env 名 ↔ 代码 `os.getenv`、litellm model_name ↔ agent model_name）。

**结论：未达 pass，需修复 R1/R2（均为 K8s 清单配置层，改动 < 10 行）后即可完结。** docker-compose 用户不受影响。

---

## ✅ review1 修复确认（全部通过）

| 项 | 状态 | 验证依据 |
|---|---|---|
| **B1** K8s Agent 端口 | ✅ 已修复 | `20-agents.yaml:74,236,396` 注入 `PORT=12001/12002/12003`（对齐 `BaseAgentSettings.port` 字段，无 `env_prefix`）；`AGENT_NAME` 已删（name 是类属性）。三 Agent + 探针 + Service targetPort 全部对齐。 |
| **B2** K8s MCP 端口 + workspace 挂载 | ✅ 已修复 | `40-mcp.yaml:64-67,167-170,262-265` env 名改为 `MCP_FILESYSTEM_PORT` / `MCP_FETCH_PORT` / `MCP_SHELL_PORT`（对齐 `mcp_servers/*/server.py`）；`40-mcp.yaml:79-93,278-293` 挂载 `a2a-prod-workspace` PVC（filesystem RW、shell RO）；`03-pvc.yaml:79-94` 新增 workspace PVC。 |
| **B3** OpenAI 兼容层强制路由 | ✅ 已修复 | `graph.py:101,130-132` `orchestrate()` 新增 `target_agent` 参数并注入 state；`classifier.py:246-254` classify 短路；`__main__.py:318,381` 同步/流式分支都透传 `target_agent`；`test_openai_compat.py:420-487` 4 个 HTTP 级测试覆盖强制路由/auto/未知兜底。 |
| **S1** glm-5 口径统一 | ✅ 已修复 | agent 代码、`infra/litellm/config.yaml`、docker-compose 全部 `glm-5`；`test_agent_card.py:213-219` 守护。 |
| **S2** orchestrator 默认 URL | ⚠️ 部分修复（见 R1） | `executor.py:41-43` 改用 service name `glm-agent:8000` + 启动 warning。compose 路径 OK，但 K8s 路径有回归（R1）。 |
| **S3** 假流式 keepalive | ✅ 已修复 | `__main__.py:375` yield `: keepalive`；`chunk_size` 20→100。 |
| **S4** API key 常数时间比较 | ✅ 已修复 | `__main__.py:26,133` `hmac.compare_digest`。 |
| **S5** request_id 唯一 | ✅ 已修复 | `a2a_client.py:92,119-123` 默认 None → uuid-based 53-bit 安全整数。 |
| **S6** OpenAI 兼容层 HTTP 测试 | ✅ 已修复 | `test_openai_compat.py:320-487` 新增 `TestChatCompletionsHTTP` + `_make_test_client` helper。 |
| **S7** MAX_FEEDBACK_ROUNDS 函数级 | ✅ 已修复 | `sdlc_workflow.py:39-50` `get_max_feedback_rounds()`；`check_blocked:334`、`aggregator.py:117` 都改调函数；`test_sdlc_workflow_utils.py:195-241` 含非数字 env 兜底测试。 |
| **S8** 多轮上下文 | ✅ 已修复 | `extract_user_query` 保留 assistant 历史（`test_openai_compat.py:240-287` 4 个多轮测试）。 |
| **S9** langfuse v3 trace 层级 | ✅ 已修复（标注） | `tracing.py:111-118` 降级时 `logger.error` + `_v3_degraded` 标志，显式说明层级未建立、ADR-0012 待补。 |
| **S10** litellm entrypoint 异常 | ✅ 已修复 | `litellm_entrypoint.py:62-79` try/except + error 上下文。 |
| **N2** emoji 误判 | ✅ 已修复 | `aggregator.py:142` 改用 `rounds >= max_rounds` 判断，不再重扫文本。 |
| **N6** 本地路径泄露 | ✅ 已修复 | `kustomization.yaml:54` 改为 generic git URL。 |

**测试回归**：`pytest tests/contract/` → **511 passed, 1 skipped**（filesystem symlink 平台 skip），无失败、无 error。

---

## 🔴 新发现的 Blocker（修复 B1 时引入的 K8s 回归）

### R1. Orchestrator 的 K8s env 名与代码读取的 env 名不匹配，orchestrator 连不上任何 Agent

**位置**：
- `infra/k8s/30-orchestrator.yaml:84-89`（注入的 env 名）
- `orchestrator/executor.py:41-43,50-52`（代码读取的 env 名）

**问题（Why）**：
K8s 清单注入：
```yaml
- name: GLM_AGENT_URL          # 30-orchestrator.yaml:84
  value: "http://glm-agent:12001/"
- name: DEEPSEEK_AGENT_URL
  value: "http://deepseek-agent:12002/"
- name: MiniMax_AGENT_URL      # 还混了大小写（MiniMax vs MINIMAX）
  value: "http://minimax-agent:12003/"
```
但代码读的是：
```python
# executor.py:41-43
"glm-agent":      os.getenv("AGENT_URLS_GLM_AGENT")      or "http://glm-agent:8000",
"deepseek-agent": os.getenv("AGENT_URLS_DEEPSEEK_AGENT") or "http://deepseek-agent:8000",
"minimax-agent":  os.getenv("AGENT_URLS_MINIMAX_AGENT")  or "http://minimax-agent:8000",
```

两套名字对不上（`GLM_AGENT_URL` ≠ `AGENT_URLS_GLM_AGENT`）→ `os.getenv` 全部返回 None → orchestrator 用默认值 `http://glm-agent:8000`。

而 B1 修复后 Agent 在 K8s 里监听 **12001/12002/12003**（`PORT` env），K8s Service 也只暴露 12001/12002/12003（`20-agents.yaml` Service `port: 12001`），**没有 8000 端口**。后果：orchestrator → `http://glm-agent:8000` → connection refused，**K8s 下三 Agent 全部不可达，所有编排请求失败**。

> 注意：注入的值本身（`http://glm-agent:12001/`）是对的，只是 env 名错了导致白注入。这是"改了值没改名"的低级笔误，但后果是部署级 Blocker。

**为什么 docker-compose 没事**：compose 里 agent 传 `PORT: 8000`（`docker-compose.yml:99`），监听 8000；S2 默认 URL `:8000` 正好匹配。只有 K8s 路径（agent 监听 12001）触发。

**建议修复**：把 `30-orchestrator.yaml:84-89` 三个 env 名改成代码读的名字（与 `executor.py:50-52` 完全一致）：
```yaml
- name: AGENT_URLS_GLM_AGENT
  value: "http://glm-agent:12001/"
- name: AGENT_URLS_DEEPSEEK_AGENT
  value: "http://deepseek-agent:12002/"
- name: AGENT_URLS_MINIMAX_AGENT      # 全大写，别再写 MiniMax
  value: "http://minimax-agent:12003/"
```
修完后 orchestrator 启动时的 `agent_urls_using_default` warning 也会消失（验证用）。

---

### R2. K8s litellm ConfigMap 里 GLM model_name 是 `glm-4.6`，与 Agent 发出的 `glm-5` 不匹配，GLM Agent 在 K8s 下路由失败

**位置**：
- `infra/k8s/02-configmap.yaml:24-26`（K8s litellm config：`model_name: glm-4.6`）
- `agents/glm_agent/agent.py:60`（Agent 发 `openai/glm-5`）
- `infra/litellm/config.yaml:12-14`（compose litellm config：`model_name: glm-5` ← 这个是对的）

**问题（Why）**：
S1 修复统一了 agent 代码与 **compose** litellm config 为 `glm-5`，但 **K8s** litellm ConfigMap（`02-configmap.yaml`，是独立的一份 litellm config，不走 `infra/litellm/config.yaml`）还是旧的 `glm-4.6`。两份 config 没同步。

后果（K8s 路径）：
1. GLM Agent 调 LiteLLM：`model=openai/glm-5`（`base_agent.py:231`）。
2. K8s litellm 只认 `glm-4.6`，找不到 `glm-5` → 返回 `404 model not found` / `LiteLLMRoutingError`。
3. GLM Agent 直接不可用（代码审查、SDLC 技术规范全挂）。

> compose 路径 litellm config 是 `glm-5`，与 agent 对齐，所以 compose 下 GLM 正常。只有 K8s 路径的 litellm config 漏改。

**建议修复**：把 `02-configmap.yaml:24-26` 的 `glm-4.6` 改成 `glm-5`，与 `infra/litellm/config.yaml` + agent 代码三方对齐：
```yaml
- model_name: glm-5
  litellm_params:
    model: openai/glm-5
    api_key: os.environ/GLM_API_KEY
    api_base: os.environ/GLM_API_BASE
```
顺带核对 `02-configmap.yaml:37-38` 的 `MiniMax_API_KEY` / `MiniMax_API_BASE`（混合大小写），LiteLLM `os.environ/` 是按字面读的，所以与 secrets.yaml.example 里的 key 名必须一字不差——建议统一全大写 `MINIMAX_API_KEY` / `MINIMAX_API_BASE`（与 docker-compose、其他 env 一致），并同步 secrets.yaml.example。这是 review1 就提过的小问题，R1 也涉及 MiniMax 大小写，建议这次一并清干净。

> 防回归建议：`test_k8s_manifests.py` 当前只做结构检查，抓不到这类语义错配。可加一个轻量测试：解析 `02-configmap.yaml` 里的 `model_name` 集合，断言与三个 Agent 的 `model_name`（`glm-5` / `deepseek-chat` / `MiniMax-M3`）完全一致。同理可加一个测试断言 orchestrator env 名与 `executor.py` 的 `os.getenv` key 一致。这两个测试能永久守住 R1/R2 这类回归。

---

## 🟡 次要问题（应修，非阻塞）

### M1. `kustomization.yaml` 注释与 resources 列表自相矛盾（secrets.yaml）

**位置**：`infra/k8s/kustomization.yaml:32`

```yaml
resources:
  - 00-namespace.yaml
  ...
  - secrets.yaml          # ⚠ Secret 单独维护（gitignore）；不在 resources 列表中   ← 注释说"不在"，但上一行就在列表里
```

`secrets.yaml` 实际**在** resources 列表里（第 32 行），但行尾注释写"不在 resources 列表中"。注释是旧版本的残留，现在矛盾。

**影响**：因为 `secrets.yaml` 在 resources 里，`kubectl apply -k` 要求该文件必须存在（它被 gitignore，只有 `secrets.yaml.example`）。deployer 若直接 `apply -k` 而没先 `cp secrets.yaml.example secrets.yaml`，会报 `evalsymlink failure ... no such file`。这是好事（fail-fast，强提醒），但注释误导会让人以为可以跳过。

**建议**：把注释改成"⚠ Secret 单独维护（gitignore）；apply -k 前必须先 `cp secrets.yaml.example secrets.yaml` 并替换 REPLACE_ME"。一句话，消除矛盾。

---

## 回归检查清单（MiniMax 修复后请逐项确认）

- [ ] **R1**：`grep -n "AGENT_URLS_\|GLM_AGENT_URL\|MiniMax_AGENT_URL" infra/k8s/30-orchestrator.yaml` → 三个 env 名应为 `AGENT_URLS_GLM_AGENT` / `AGENT_URLS_DEEPSEEK_AGENT` / `AGENT_URLS_MINIMAX_AGENT`，值 `http://<svc>:1200X/`。
- [ ] **R2**：`grep -n "glm-" infra/k8s/02-configmap.yaml` → 应为 `glm-5`（非 glm-4.6）；MiniMax env 名全大写。
- [ ] **R1/R2 防回归测试**：新增（a）litellm config model_name 与 agent model_name 对齐测试；（b）orchestrator env 名与 `executor.py` getenv key 对齐测试。
- [ ] **M1**：`kustomization.yaml:32` 注释消除矛盾。
- [ ] 全量回归：`.venv-prod/Scripts/python -m pytest tests/contract/ -q` 仍为全绿（含新增的 2 个防回归测试）。

---

## 结语

第一轮的三个 Blocker 修得非常干净，测试纪律也好（每个修复都配测试 + 溯源注释），这是高质量修复。R1/R2 本质是同一个根因的两次发作：**"K8s 清单是独立维护的一份配置，改了 agent 代码 / compose 后忘了同步 K8s 清单"**。两处改动加起来 < 10 行 yaml，修掉后再跑一遍契约测试即可完结。

docker-compose 路径（本地开发）已经完全 clean，可以放心用；K8s 路径修完 R1/R2 后再上一轮我就给 PASS，项目完结。

— GLM-5.2
