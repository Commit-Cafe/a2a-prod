# Model 名映射（Open WebUI ↔ Orchestrator）

## 当前实现

| Open WebUI model 名 | 实际路由 | 后端实现 |
|---|---|---|
| `glm-agent` | GLM-5.1 Agent | `agents/glm_agent/agent.py` |
| `deepseek-agent` | DeepSeek Agent | `agents/deepseek_agent/agent.py` |
| `minimax-agent` | MiniMax Agent | `agents/minimax_agent/agent.py` |
| `auto` | 由 Orchestrator 分类器决定 | `orchestrator/classifier.py` |

## 取名约定

- 使用 **kebab-case**（连字符），与 Open WebUI 默认下拉框风格一致
- 后缀 `-agent` 与 `a2a-prod` 项目的 Agent 目录名对齐
- 未来新增 Agent 时，直接在 `orchestrator/openai_compat.py::SUPPORTED_MODELS`
  里加一行；`list_models()` 会自动反映到 `/v1/models` 端点

## 添加新 Agent 的步骤

1. 在 `agents/<name>_agent/` 目录下实现 Agent（参考 `agents/glm_agent/`）
2. 在 `infra/docker-compose.yml` 加新服务
3. 在 `orchestrator/openai_compat.py::SUPPORTED_MODELS` 添加新 model id
4. 在 `orchestrator/graph.py` 把新 Agent 注册到 `AGENT_REGISTRY`
5. 更新本文档
6. 加 E2E 测试（参考 `tests/test_p5_e2e.py`）

## 不做 model 别名

- OpenAI 官方允许 ``"gpt-4"`` ↔ ``"gpt-4-0613"`` 别名互通
- a2a-prod **不**做别名（避免意外路由到错误 Agent）
- 如确需兼容老客户端，单独走迁移窗口 + 文档
