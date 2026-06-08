# CODESTYLE — a2a-prod 代码风格

> 强制规范。任何 PR 必须先通过 `ruff check` + `ruff format --check` + `mypy`。
> 规则集与 pyproject.toml 的 `[tool.ruff]` / `[tool.mypy]` 一致。

---

## 1. Python 基础

- **版本**：3.12+（`.python-version` 锁定）
- **包管理**：`uv`（不用 pip / poetry）
- **格式化**：`ruff format`（不用 black）
- **Lint**：`ruff check`
- **类型检查**：`mypy --strict`（公共 API 必须，内部辅助代码可 `# type: ignore[xxx]` 标注）
- **导入排序**：`ruff` 默认 `isort` 规则

---

## 2. 命名约定

| 类型 | 风格 | 示例 |
|---|---|---|
| 模块文件 | snake_case | `base_agent.py` |
| 包目录 | snake_case | `glm_agent/` |
| 类 | PascalCase | `GLMAgent`、`AgentSettings` |
| 函数 / 方法 | snake_case | `send_message`、`_handle_task` |
| 常量 | UPPER_SNAKE | `DEFAULT_PORT = 12001` |
| 私有 | 前缀 `_` | `_build_prompt()` |
| 类型别名 | PascalCase + 后缀 | `MessageDict`、`TaskState` |
| Pydantic 模型 | PascalCase | `AgentCard`、`MessageSendParams` |

**禁止**：
- 单字母变量（`i`, `j` 除外）
- 匈牙利命名（`strName`）
- 缩写不一致（统一 `idx` 不混用 `i/index`）

---

## 3. 目录与文件组织

### 3.1 一个模块只做一件事

| 文件 | 职责 | 行数上限 |
|---|---|---|
| `agent.py` | Agent 主体逻辑（ADK LlmAgent 定义 + A2A 暴露） | ≤ 200 |
| `__main__.py` | CLI 入口（仅解析参数 + 调用 agent.py） | ≤ 50 |
| `config.py` / `settings.py` | pydantic-settings 配置 | ≤ 100 |
| `models.py` | 数据模型 / DTO | ≤ 200 |
| `tools.py` | 工具函数集合 | ≤ 300 |

**超出上限必须拆分**，并在 PR 描述里说明拆分依据。

### 3.2 目录扁平化
- 单层目录优先，嵌套不超过 3 层
- `agents/glm_agent/agent.py` ✓
- `agents/glm_agent/internal/handlers/task.py` ✗ 太深

---

## 4. 函数与类设计

### 4.1 函数
- **长度**：≤ 50 行（含空行注释），超出必须拆分
- **参数**：≤ 5 个，超过用 `dataclass` 或 `TypedDict` 包装
- **单一职责**：一个函数只做一件事
- **类型注解**：所有公共函数必须完整类型注解

```python
# ✓ 好
async def send_message(
    agent: BaseAgent,
    message: Message,
    *,
    timeout: float = 30.0,
) -> TaskResult:
    ...

# ✗ 不好
def send(agent, msg, t=30):  # 缺类型、参数名缩写、默认值含义不明
    ...
```

### 4.2 类
- **优先组合而非继承**（BaseAgent 例外，作为统一抽象）
- **Pydantic 优于 dataclass**（自动校验 + JSON 序列化）
- **不写 Singleton**（用依赖注入）
- **公共方法先于私有方法**

### 4.3 异步
- **统一 async def**（不混用同步 / 异步）
- **httpx.AsyncClient 模块级共享**（不每次新建）
- **不阻塞事件循环**：CPU 密集任务走 `asyncio.to_thread`

---

## 5. 错误处理

### 5.1 三层原则
- **边界层**（HTTP / RPC 入口）：try/except 全捕获，转 A2A 标准错误码
- **业务层**：抛领域异常（`AgentError`、`LLMTimeout`）
- **基础设施层**：抛原生异常（`httpx.TimeoutException`）

### 5.2 禁止
- `except:` 裸捕获
- `except Exception: pass` 静默
- 在循环内 try/except 不记日志

```python
# ✓ 好
try:
    result = await self.llm.generate(prompt)
except httpx.TimeoutException as e:
    logger.warning("llm_timeout", extra={"model": self.model})
    raise LLMTimeout(self.model) from e

# ✗ 不好
try:
    result = await self.llm.generate(prompt)
except:
    pass
```

---

## 6. 日志

### 6.1 统一 structlog
```python
import structlog
logger = structlog.get_logger(__name__)

logger.info("agent_started", port=12001, model="glm-5.1")
logger.warning("llm_timeout", model="glm-5.1", timeout=30)
```

### 6.2 字段约定
- 每条日志必须有 `request_id`（中间件注入）
- 不用 f-string，用结构化字段
- 不打印敏感字段（`api_key`、`token`）

---

## 7. 测试

### 7.1 命名
- 单元测试：`tests/unit/test_<module>.py`
- e2e 测试：`tests/test_p<phase>_e2e.py`
- 函数：`test_<动作>_<预期>`，如 `test_send_message_returns_task`

### 7.2 必须覆盖
- 每个 Agent 的 Agent Card 暴露
- 每个 Agent 的 message/send 基础响应
- docker-compose 起服务后的健康检查

### 7.3 不测
- 第三方库内部
- LiteLLM 内部路由

---

## 8. 注释与文档

### 8.1 必须有 docstring
- 所有公共函数 / 类 / 模块
- 三引号 Google 风格

```python
def send_message(agent: BaseAgent, message: Message) -> TaskResult:
    """向 Agent 发送 A2A 消息并等待结果。

    Args:
        agent: 目标 Agent 实例。
        message: A2A 消息体。

    Returns:
        TaskResult 包含最终状态与消息。

    Raises:
        LLMTimeout: LLM 调用超时。
        AgentError: Agent 内部错误。
    """
```

### 8.2 注释解释"为什么"，不解释"是什么"
```python
# ✓ 好
# LiteLLM 用 model 名做路由，前缀 openai/ 表示走 OpenAI 兼容协议
litellm_model = f"openai/{self.model_name}"

# ✗ 不好
# 给变量赋值
litellm_model = f"openai/{self.model_name}"
```

---

## 9. Git 提交

### 9.1 Commit message（Conventional Commits）
```
feat(agent): add glm agent with adk+a2a-sdk
fix(litellm): correct deepseek api base url
docs(arch): update P2 orchestrator design
refactor(base): extract common agent setup
test(p1): add e2e for three agents
chore(infra): bump litellm to 1.55
```

### 9.2 PR 标题
- `<type>(<scope>): <subject>`
- subject ≤ 50 字符
- 不在标题写 period

---

## 10. 配置与密钥

- 所有配置走 `pydantic-settings` + `.env`
- `.env` **永远**在 `.gitignore` 里
- `.env.example` 必须随代码同步
- 启动时跑 `scripts/check_env.py` 预检

---

## 11. 依赖管理

- 所有依赖写在 `pyproject.toml` 的 `[project.dependencies]`
- 版本用 `>=` 下限 + `<` 上限，不用 `^`（uv 友好）
- 新增依赖必须在 PR 描述里写"为什么需要"
- 每月跑一次 `uv lock --upgrade`

---

**最后更新**：2026-06-05
