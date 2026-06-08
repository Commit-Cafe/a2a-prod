# OpenAI 兼容契约（SPEC §3.9 / ADR-0009）

> 本文档描述 Orchestrator 对外暴露的 OpenAI 兼容端点契约。
> Open WebUI 与其他 OpenAI 客户端均按本文档对接。

## 端点列表

| 端点 | 方法 | 鉴权 | 说明 |
|---|---|---|---|
| `/v1/models` | GET | Bearer | 列出可路由的 3 个 Agent |
| `/v1/chat/completions` | POST | Bearer | 同步 chat completion |
| `/v1/chat/completions` | POST + `stream=true` | Bearer | 流式（SSE） |

注：本 Orchestrator **不**实现以下端点（Open WebUI 也不会用到）：
- `/v1/embeddings`
- `/v1/images/*`
- `/v1/audio/*`
- `/v1/files/*`

如客户端调用，返回 404。

## 鉴权

所有端点要求 `Authorization: Bearer <key>` 头。`<key>` 必须与以下环境变量之一一致：

- `ORCHESTRATOR_API_KEY`（优先级最高）
- `LITELLM_MASTER_KEY`（兜底）

未设环境变量时跳过鉴权（开发模式）。

错误响应：
- 缺头 / 格式错 → 401 `{"detail":"missing or malformed Authorization header"}`
- key 不对 → 401 `{"detail":"invalid api key"}`

## `/v1/models`

请求：
```http
GET /v1/models
Authorization: Bearer <key>
```

响应：
```json
{
  "object": "list",
  "data": [
    {"id": "glm-agent",     "object": "model", "created": 1717800000, "owned_by": "a2a-prod"},
    {"id": "deepseek-agent", "object": "model", "created": 1717800000, "owned_by": "a2a-prod"},
    {"id": "minimax-agent",  "object": "model", "created": 1717800000, "owned_by": "a2a-prod"}
  ]
}
```

Open WebUI 启动时会调此端点拉取 model 列表，下拉框显示这 3 个 + `auto`。

## `/v1/chat/completions`

请求：
```json
{
  "model": "auto",
  "messages": [
    {"role": "system", "content": "你是助手"},
    {"role": "user",   "content": "对比 Python 与 Go 的并发模型"}
  ],
  "stream": false,
  "temperature": 0.7,
  "max_tokens": 2048
}
```

`model` 字段说明（SPEC §3.9.3）：

| 取值 | 行为 |
|---|---|
| `glm-agent` | 强制 DIRECT 模式路由到 GLM Agent |
| `deepseek-agent` | 强制 DIRECT 模式路由到 DeepSeek Agent |
| `minimax-agent` | 强制 DIRECT 模式路由到 MiniMax Agent |
| `auto`（默认） | Orchestrator 按关键词自主分类（DIRECT 单 Agent / DECOMPOSE 多 Agent） |
| 其他值 | 兜底走 `auto`（避免 Open WebUI 自定义 model 名导致 400） |

### 同步响应

```json
{
  "id": "chatcmpl-3a1f8b9c7d2e4f5a6b8c9d0e1f2a3b4c",
  "object": "chat.completion",
  "created": 1717800123,
  "model": "auto",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "对比结果..."},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

`usage` 字段为占位值（本阶段不实现真实计费；LiteLLM 端才有真实数据）。

### 流式响应（SSE）

`Content-Type: text/event-stream`，每行以 `data: ` 开头，格式：

```
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1717800123,"model":"auto","choices":[{"index":0,"delta":{"content":"你"},"finish_reason":null}]}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk",...}

...

data: [DONE]
```

第一块 `delta` 为空（标识 assistant 角色开始），
最后一块 `finish_reason="stop"`，
收尾 `data: [DONE]`。

## 错误码

| HTTP | 触发条件 | 响应体 |
|---|---|---|
| 400 | messages 为空 / 无 user role | `{"detail": "messages must contain at least one 'user' role message"}` |
| 401 | 鉴权失败 | `{"detail": "..."}` + `WWW-Authenticate: Bearer` |
| 500 | Orchestrator 内部错误 | `{"detail": "chat completion failed: <type>: <msg>"}` |

## 与 OpenAI 官方差异

| 维度 | OpenAI 官方 | a2a-prod |
|---|---|---|
| `usage` | 真实 token 计数 | 占位 0（P5.1 升级） |
| `finish_reason=length` | token 截断 | 本阶段不会触发（A2A 端无 max_tokens 透传） |
| `tool_calls` | 支持 | 不支持（P5.1 评估） |
| `function_call` | 已废弃 | 不支持 |
| multimodal `content` | 支持 list[Part] | 仅支持 str（P5.1 评估） |
| `n>1`（多 choice） | 支持 | 仅 1 choice |
| `logprobs` | 支持 | 不支持 |

## 实施引用

- Orchestrator 实现：[orchestrator/openai_compat.py](../../../orchestrator/openai_compat.py)
- FastAPI 端点：[orchestrator/__main__.py:chat_completions_endpoint](../../../orchestrator/__main__.py)
- 契约测试：[tests/contract/test_openai_compat.py](../../../tests/contract/test_openai_compat.py)
- E2E 测试：[tests/test_p5_e2e.py](../../../tests/test_p5_e2e.py)
