# SDLC Workflow（研发协作工作流）设计文档

> **阶段**：P2.1（在 P0~P6 基础上的增量能力）
> **状态**：设计已确认，待实施
> **日期**：2026-06-17
> **作者**：a2a-prod 团队
> **关联**：[NORTH_STAR.md](../../NORTH_STAR.md) / [SPEC.md](../../SPEC.md) / [ARCHITECTURE.md](../../ARCHITECTURE.md)

---

## 0. TL;DR

实装 **WORKFLOW 编排模式**的第一个具体实例——**SDLC（软件研发协作）工作流**，让三家国产模型按"产品 → 架构 → 实现 → 反馈"顺序协作，产出可落盘的工程产物：

```
用户提研发需求
   ↓
[DeepSeek] 产 Spec/PRD 文档          → workspace/sdlc/<sid>/spec.md
   ↓
[GLM-5.2]  产技术规范 + 编码指令      → workspace/sdlc/<sid>/tech-design.md
              （严禁写实现代码）
   ↓
[MiniMax]  按规范写代码 + 自跑 pytest → workspace/sdlc/<sid>/code/*
              （严格遵循上游文档，遇阻 [NEED_HELP]，禁自由发挥）
   ↓
check_blocked: [NEED_HELP] & rounds < MAX_ROUNDS(=2)?
   ├─ 是 → [GLM-5.2] 给指导（不写码）→ 回 MiniMax 重写
   └─ 否 → aggregate → END
```

**核心约束（角色边界）**：

| Agent | 该做 | 绝不做 |
|---|---|---|
| DeepSeek | 写 PRD / Spec / 需求文档 | 写实现代码 |
| GLM-5.2 | 写技术规范 / 方案路线 / 审查 / **给 MiniMax 编码指令** | **写实现代码**（只"叫 MiniMax 写"） |
| MiniMax | 写代码 + 自跑 pytest | **遇阻自行解决**（必须 [NEED_HELP] 反馈）/ **自由发挥**（必须严格遵循上游文档） |

---

## 1. 目标与边界

### 1.1 目标

在 a2a-prod 现有 P0~P6 体系上新增 P2.1 能力：让三家国产模型按"产品→架构→实现→反馈"顺序协作，端到端产出可落盘的工程产物（Spec 文档 + 技术规范 + 代码 + 单元测试）。

### 1.2 做（In Scope）

- 新增 `OrchestrationMode.WORKFLOW`（解除 `state.py` 里被注释的 WORKFLOW 占位）
- 新增 `sdlc_workflow.py` 子图（5 节点）+ 4 段 prompt 模板 + workspace 落盘辅助
- classifier 新增 WORKFLOW 路由分支（关键词 + 正则）
- 主图集成 `workflow_execute` 节点（子图独立 compile，共享主图 state schema）
- aggregate 新增 WORKFLOW 聚合分支
- 三家 Agent system prompt 增量更新（第二道防线）
- 契约测试（9 个，不依赖 LLM/docker）+ e2e 测试（3 个，依赖 docker 栈 + API Key）
- 文档同步（SPEC / ARCHITECTURE / DECISIONS / NORTH_STAR）

### 1.3 不做（Out of Scope — YAGNI）

- ❌ OpenAI 兼容层（`/v1/chat/completions`）支持 WORKFLOW（留 P2.2）
- ❌ 多轮无限反馈循环（已定单轮上限 `MAX_FEEDBACK_ROUNDS = 2`）
- ❌ 动态 workflow 引擎 / YAML DSL（只有 1 个工作流，引擎抽象属过度工程）
- ❌ 独立 `/v1/sdlc` 端点（沿用 `/v1/orchestrate`）
- ❌ 多个 workflow 模板（只做"研发协作"这一个）
- ❌ Web UI 可视化工作流进度（留 P5.1）
- ❌ workspace 产物版本管理 / diff（留 P6.1）
- ❌ 跨 session 的工作流状态持久化（InMemory 即可）

### 1.4 验收标准（Definition of Done）

- [ ] 9 个契约测试全绿（无 docker，秒级）
- [ ] 3 个 e2e 测试全绿（需 docker 栈 + 三家 API Key）
- [ ] 现有 P1~P5 e2e 无回归（classifier/graph/aggregator contract 同步更新后）
- [ ] `mypy --strict` 通过（新增模块）
- [ ] `ruff check` + `ruff format --check` 通过
- [ ] 文档同步完成

---

## 2. 架构

### 2.1 WORKFLOW 子图结构

```
START
  │
  ▼
deepseek_doc ──────► glm_spec ──────► minimax_code ──────► check_blocked ◄─────────┐
  (DeepSeek          (GLM-5.2         (MiniMax              │                        │
   产 PRD/Spec)       产技术规范,      写代码 +             ├─ NEED_HELP &           │
                      严禁代码)        跑 pytest,           │ round<MAX ──► glm_feedback
                                      严格遵循上游)        │                        │
                                                          │                        │
                                                          ├─ pass / 达上限 ──► aggregate → END
                                                          │
                                                          └─ (附 errors 如有)
```

### 2.2 节点职责清单

| 节点 | 执行 Agent | 输入 | 输出（落盘 + state） |
|---|---|---|---|
| `deepseek_doc` | deepseek-agent | `user_query` | PRD/Spec → `workspace/sdlc/<sid>/spec.md` + `state["sdlc_doc"]["spec"]` |
| `glm_spec` | glm-agent | spec + user_query | 技术规范 + **对 MiniMax 的编码指令** → `tech-design.md` + `state["sdlc_doc"]["tech_design"]` |
| `minimax_code` | minimax-agent | tech_design + 编码指令 + 反馈历史（如有） | 实现 + **自跑 pytest 结果** → `implementation.md` + `code/*` + `state["sdlc_doc"]["implementation"]` |
| `glm_feedback` | glm-agent | minimax 的 `[NEED_HELP]` 描述 + 历史 | **指导/澄清/决策**（非代码）→ `state["sdlc_feedback"][round]`，回到 minimax_code |
| `check_blocked` | （纯路由，无 LLM） | minimax 输出 + feedback_rounds | 路由决策（`"glm_feedback"` 或 `"done"`） |
| `aggregate` | （复用现有） | sdlc_doc 各阶段产物 | `final_answer`（拼接 spec/design/code/feedback） |

### 2.3 与主图集成

主图保持线性汇聚到 `aggregate`，SDLC 业务复杂度全部封装在 `workflow_execute` 节点 + 子图里：

```
START → classify → ┬─ direct      → direct_execute   ─┐
                   ├─ decompose   → decompose_execute ─┤→ aggregate → END
                   └─ workflow    → workflow_execute  ─┘
                                              │
                                  ┌───────────┴───────────┐
                                  │ SDLC 子图（5 节点）   │
                                  └───────────────────────┘
```

### 2.4 角色边界约束（三层防御）

LLM 不遵守 prompt 是常态，用三层防御：

1. **Prompt 层**（`sdlc_prompts.py`）：每个节点的 prompt 顶部写死硬约束
2. **节点层**（`sdlc_workflow.py` 兜底校验）：节点返回后做后处理
   - GLM 节点：剥离超长代码块（> 15 行，区分"类型签名/伪代码" vs "完整实现"）
   - MiniMax 节点：检测 `[NEED_HELP]` 标记 + 检测自由发挥语义（"我额外/我觉得/我建议加"）
3. **Agent instruction 层**（三家 Agent 的 `default_instruction`）：第二道防线，防止用户直接调 Agent 单点时也偷跑

### 2.5 方案选型（对比 3 种编排方式）

| 方案 | 描述 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| **A. 静态 StateGraph 子图** | LangGraph StateGraph 建固定研发流程子图 | 与现有 `graph.py` 风格一致；复用 trace/checkpoint；可测 | 流程写死，换模板要改图 | **✅ 选定** |
| B. 动态 workflow 引擎 | 通用引擎按 `WorkflowSpec` 执行 | 换模板不改代码 | 过度工程（YAGNI）；测试更难 | ❌ |
| C. 手写 async 协程 | 不走 LangGraph，手动 await 三家 Agent | 实现最快 | 失去 trace/checkpoint；与 P2 不一致 | ❌ |

---

## 3. State Schema 扩展

### 3.1 `OrchestrationMode` 枚举解除注释

```python
# orchestrator/state.py

class OrchestrationMode(StrEnum):
    DIRECT = "direct"
    TASK_DECOMPOSITION = "task_decomposition"
    WORKFLOW = "workflow"          # 新增（P2.1）
    # NEGOTIATION / 通用 WORKFLOW 模式仍留 P2.2+
```

> 原 `state.py` 注释里 `WORKFLOW` 是泛化"通用工作流"占位，本阶段实装的是它的第一个具体实例（SDLC），枚举值复用 `"workflow"` 不冲突。

### 3.2 新增 TypedDict

```python
class SdlcDoc(TypedDict, total=False):
    """SDLC 工作流各阶段产出（每个 value 是 Agent 输出的 markdown 文本）。"""
    spec: str              # DeepSeek 产出的 PRD/Spec
    tech_design: str       # GLM-5.2 产出的技术规范 + 编码指令
    implementation: str    # MiniMax 产出的实现说明（含 pytest 结果）
    code_paths: list[str]  # MiniMax 落盘的代码文件相对路径（workspace 内）


class SdlcFeedback(TypedDict, total=False):
    """单轮反馈记录。"""
    round: int             # 第几轮（1-based）
    blocker: str           # MiniMax 的 [NEED_HELP] 问题描述
    guidance: str          # GLM-5.2 给出的指导（非代码）
```

### 3.3 `OrchestrationState` 新增字段

```python
class OrchestrationState(TypedDict, total=False):
    # ... 现有字段不动 ...

    # ---- WORKFLOW 模式专用（P2.1） ----
    sdlc_doc: SdlcDoc              # 各阶段产出文档
    sdlc_feedback: list[SdlcFeedback]  # 每轮反馈记录
    feedback_rounds: int           # 已发生的反馈轮数（0/1/2，上限 MAX_ROUNDS=2）
    workflow_status: str           # "running" / "blocked_resolved" / "blocked_unresolved"
```

全部可选（`total=False`），不影响 DIRECT/DECOMPOSE 模式。

### 3.4 常量

```python
# orchestrator/sdlc_workflow.py 顶部

# 反馈上限：写死默认值 + 环境变量覆盖口子
MAX_FEEDBACK_ROUNDS = int(os.getenv("SDLC_MAX_FEEDBACK_ROUNDS", "2"))

# MiniMax 遇阻标记（节点函数硬校验识别，LLM 不听话也能识别）
NEED_HELP_MARKER = "[NEED_HELP]"

# MiniMax 落盘文件清单标记（强约定，便于解析）
FILES_WRITTEN_MARKER = "[FILES_WRITTEN]"

# GLM 兜底校验：代码块超过此行数视为"完整实现"，剥离
GLM_CODE_BLOCK_MAX_LINES = 15

# 子图执行超时（5+ 次 LLM 调用 × 60s 上限）
SDLC_TIMEOUT_SECONDS = int(os.getenv("SDLC_TIMEOUT_SECONDS", "600"))
```

### 3.5 State 流转示例（一轮带反馈的场景）

```
初始:          {user_query, session_id}
deepseek_doc:  → {sdlc_doc: {spec}, workflow_status: "running"}
glm_spec:      → {sdlc_doc: {spec, tech_design}}
minimax_code:  → {sdlc_doc: {spec, tech_design, implementation, code_paths}, feedback_rounds: 0}
check_blocked: [NEED_HELP] in implementation & rounds<2 → route "glm_feedback"
glm_feedback:  → {sdlc_feedback: [{round:1, blocker, guidance}], feedback_rounds: 1}
minimax_code:  → {sdlc_doc.implementation 更新, code_paths 更新}
check_blocked: 无 [NEED_HELP] → route "done"
aggregate:     → {final_answer: 拼接 4 段 + 状态总结}
```

### 3.6 LangGraph reducer 兼容性

`sdlc_doc`（dict）和 `sdlc_feedback`（list）的更新策略：**节点自己 merge 后返回完整值**，不引入自定义 reducer。

- `minimax_code` 重写 `sdlc_doc` 时：从 state 读旧 dict → 合并新字段 → 返回完整新 dict
- `glm_feedback` 追加 feedback 时：从 state 读旧 list → append 新元素 → 返回完整新 list

与现有 `agent_responses` / `errors` 处理方式一致，无新概念。

---

## 4. 节点实现 + Prompt 模板

### 4.1 文件布局

```
orchestrator/
├── sdlc_workflow.py        # 新增：WORKFLOW 子图 + 5 个节点 + 兜底校验工具
├── sdlc_prompts.py         # 新增：4 段 prompt 模板
├── sdlc_workspace.py       # 新增：workspace 落盘辅助
├── graph.py                # 改：新增 workflow_execute 节点 + 路由分支
├── classifier.py           # 改：新增 WORKFLOW 关键词识别
├── aggregator.py           # 改：新增 _aggregate_workflow 分支
└── state.py                # 改：新增 SdlcDoc/SdlcFeedback/WORKFLOW 字段
```

拆 3 个新文件而非全塞 `sdlc_workflow.py`：遵循 CODESTYLE §1.3（模块 ≤ 300 行），prompt 模板和落盘逻辑独立可测。

### 4.2 节点签名

```python
# orchestrator/sdlc_workflow.py

async def deepseek_doc(state: OrchestrationState) -> dict[str, Any]:
    """节点 1：DeepSeek 产 PRD/Spec。

    1. 拼 prompt（sdlc_prompts.DEEPSEEK_SPEC_PROMPT + user_query）
    2. a2a_client.message_send(deepseek_url, prompt)
    3. 落盘 workspace/sdlc/<sid>/spec.md
    4. 返回 {"sdlc_doc": {"spec": text}, "workflow_status": "running"}
    """

async def glm_spec(state: OrchestrationState) -> dict[str, Any]:
    """节点 2：GLM-5.2 产技术规范 + 编码指令（禁代码）。

    1. 拼 prompt（GLM_TECH_DESIGN_PROMPT + spec + user_query）
    2. a2a_client.message_send(glm_url, prompt)
    3. 兜底校验：剥离超长代码块（_strip_oversized_code_from_glm）
    4. 落盘 tech-design.md
    5. 返回 {"sdlc_doc": {**old, "tech_design": text}}
    """

async def minimax_code(state: OrchestrationState) -> dict[str, Any]:
    """节点 3：MiniMax 写代码 + 自跑 pytest；遇阻标 [NEED_HELP]；禁自由发挥。

    1. 拼 prompt（MINIMAX_CODE_PROMPT + tech_design + 编码指令 + 反馈历史如有）
    2. a2a_client.message_send(minimax_url, prompt)
       注：MiniMax Agent 已注入 filesystem+shell MCP，会自行落盘 + run_command pytest
    3. 解析输出：检测 [NEED_HELP]，提取 code_paths（[FILES_WRITTEN] 标记 + 目录扫描兜底）
    4. 检测自由发挥语义（_detect_freestyle）
    5. 返回 {"sdlc_doc": {**old, "implementation": text, "code_paths": paths}}
    """

def check_blocked(state: OrchestrationState) -> str:
    """节点 4（纯路由）：判定走 glm_feedback 还是 aggregate。

    Returns:
        "glm_feedback" 或 "done"
    """
    impl = state.get("sdlc_doc", {}).get("implementation", "")
    rounds = state.get("feedback_rounds", 0)
    # 遇阻判定：显式 [NEED_HELP] 标记 OR 自由发挥被检测
    blocked = NEED_HELP_MARKER in impl or _detect_freestyle(impl) is not None
    if blocked and rounds < MAX_FEEDBACK_ROUNDS:
        return "glm_feedback"
    return "done"

async def glm_feedback(state: OrchestrationState) -> dict[str, Any]:
    """节点 5：GLM-5.2 给指导（禁代码），回到 minimax_code。

    1. 提取 blocker：从 implementation 里截 [NEED_HELP] 后的内容，
       或 _detect_freestyle 返回的自由发挥问题描述
    2. 拼 prompt（GLM_FEEDBACK_PROMPT + blocker + tech_design + 历史 feedback）
    3. a2a_client.message_send(glm_url, prompt)
    4. 兜底校验：剥离超长代码块
    5. 返回 {"sdlc_feedback": old + [{"round": rounds+1, "blocker", "guidance"}],
              "feedback_rounds": rounds + 1}
    """
```

### 4.3 Prompt 模板（`sdlc_prompts.py`）

#### `DEEPSEEK_SPEC_PROMPT`（节点 1）

```
你是团队的产品经理 + 技术总监。当前任务是从用户需求产出 Spec 文档。

【用户原始需求】
{user_query}

【你的产出：PRD/Spec 文档】（用 markdown，含以下小节）
1. 需求复述（一句话总结 + 验证理解）
2. 澄清问题（列出关键不确定点，能合理假设的给出假设）
3. MVP 范围（明确做什么、不做什么）
4. 验收标准（可测试的 checklist）
5. 非目标（明确划掉的范围）

【硬约束】
- 你不写实现代码（那是 MiniMax 的工作）
- 你不审代码（那是 GLM 的工作）
- 输出纯文档，不要 ``` 代码块
- 用中文回复，技术术语保留英文
```

#### `GLM_TECH_DESIGN_PROMPT`（节点 2，**硬禁代码**）

```
你是团队的技术总监。基于以下 Spec 产出技术规范，并给 MiniMax 下编码指令。

【Spec 文档】
{spec}

【用户原始需求】
{user_query}

【你的产出】（用 markdown）
1. 架构与组件划分
2. 核心数据结构 / 接口契约（用伪代码或类型签名，不要完整实现）
3. 代码风格与规范（命名、错误处理、日志、测试约定）
4. 方案路线（推荐方案 + 备选 + 权衡）
5. 审查清单（MiniMax 提交后你将按此验收）
6. 【对 MiniMax 的编码指令】明确列出：要实现哪些文件、每个文件的职责、必须附的 pytest 用例

【硬约束 — 违反将导致工作流回滚】
- 你**严禁输出可执行的实现代码**（不要写 ```python ``` 完整函数体）
- 你只能描述"要 MiniMax 实现什么"，不能替它实现
- 接口契约用类型签名 / 伪代码，不用完整实现
- 如果发现自己在写完整代码，立即改为："MiniMax，请在 <file> 中实现 <描述>"
```

#### `MINIMAX_CODE_PROMPT`（节点 3，**硬约束遇阻反馈 + 严格遵循上游**）

```
你是团队的程序员。基于以下技术规范与编码指令写代码。

【技术规范】
{tech_design}

【之前 GLM 的反馈】（如有，{feedback_history}；无则空）
{feedback_block}

【你的任务】
1. 严格按【对 MiniMax 的编码指令】实现，每个文件用 write_file 落盘到 workspace/sdlc/{session_id}/code/
2. 写完必须用 run_command 跑 pytest 自验（命令：pytest workspace/sdlc/{session_id}/code/ -v）
3. 把 pytest 输出附在最后
4. 输出末尾用一行标记落盘文件：[FILES_WRITTEN] path1, path2, ...

【硬约束 — 违反将导致返工】
- **严格遵循 Spec 和技术规范，禁止自由发挥**
  - 文件结构、模块划分、接口签名、命名、测试约定 → 必须与 GLM 技术规范一致
  - 不得自行新增 Spec/技术规范未提到的功能、依赖、文件
  - 不得自行更改 GLM 指定的方案路线
- **遇到任何问题（需求不清 / pytest 失败 / 技术选型不确定）→ 立即停止，不要自己硬解**
  - 在输出**第一行**写：`[NEED_HELP] <一句话问题描述>`
  - 然后简述：你已经尝试了什么、卡在哪、需要 GLM 给什么指导
  - 不要试图绕过问题（如注释掉失败的测试、try/except 吞异常、改需求）
- 等待 GLM 指导后你会被重新调用
```

#### `GLM_FEEDBACK_PROMPT`（节点 5，**只给指导不写码**）

```
你是团队的技术总监。MiniMax 在执行你下的编码指令时遇到了阻塞，需要你给指导。

【MiniMax 的阻塞描述】
{blocker}

【你的原始技术规范】（供参考）
{tech_design}

【之前已给过的反馈】（避免重复）
{prior_feedback}

【你的产出】
给 MiniMax 清晰、可执行的指导：澄清需求 / 决策选型 / 给出修复方向 / 调整规范。
可以给**关键代码片段**（< 10 行的示意），但**不要给完整实现**——MiniMax 必须自己写。

【硬约束】
- 不要替 MiniMax 写完整代码
- 指导要具体到"改哪个文件 / 哪个函数 / 改成什么方向"
- 如果是规范本身有问题，明确说"我修改规范为：..."
```

### 4.4 节点级兜底校验

```python
# orchestrator/sdlc_workflow.py 内部工具

_CODE_BLOCK_RE = re.compile(
    r"```(?:python|py|go|java|js|ts|rust)?\n.*?\n```", re.DOTALL
)

# MiniMax 自由发挥语义标记（检测到 → 视同遇阻，强制反馈 GLM）
_FREESTYLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"我额外(加|实现|新增)"),
    re.compile(r"我觉得(应该|可以)"),
    re.compile(r"我建议(增加|加|用)"),
    re.compile(r"我自己(加|实现|改)"),
    re.compile(r"自行(新增|添加|决定)"),
)


def _strip_oversized_code_from_glm(text: str, *, max_lines: int = GLM_CODE_BLOCK_MAX_LINES) -> str:
    """GLM 节点输出后调用：若代码块超过 max_lines，剥离并打 warning。

    保留短的类型签名 / 伪代码（≤ max_lines），剥离完整实现。
    被剥离的位置替换为：「（GLM 应委派 MiniMax 实现：<首行摘要>…）」
    """
    def _replace(m: re.Match) -> str:
        block = m.group(0)
        line_count = block.count("\n")
        if line_count <= max_lines:
            return block  # 短块保留（类型签名 / 伪代码）
        first_line = block.split("\n")[1][:60] if "\n" in block else ""
        logger.warning("glm_code_block_stripped", lines=line_count, preview=first_line)
        return f"（GLM 应委派 MiniMax 实现：{first_line}…）"

    return _CODE_BLOCK_RE.sub(_replace, text)


def _extract_need_help(implementation: str) -> str | None:
    """从 MiniMax 输出提取 [NEED_HELP] 后的 blocker 描述。"""
    if NEED_HELP_MARKER not in implementation:
        return None
    idx = implementation.index(NEED_HELP_MARKER)
    rest = implementation[idx + len(NEED_HELP_MARKER):].strip()
    # 取标记后到下一个空行或结尾
    return rest.split("\n\n")[0].strip() or None


def _detect_freestyle(implementation: str) -> str | None:
    """检测 MiniMax 自由发挥语义，返回问题描述（用于反馈）；合规返回 None。"""
    for pattern in _FREESTYLE_PATTERNS:
        m = pattern.search(implementation)
        if m:
            # 取匹配处上下文作为问题描述
            start = max(0, m.start() - 10)
            end = min(len(implementation), m.end() + 40)
            return f"检测到自由发挥：...{implementation[start:end]}..."
    return None


def _extract_files_written(implementation: str, session_id: str) -> list[str]:
    """从 MiniMax 输出提取落盘文件路径。

    策略（a）+[FILES_WRITTEN] 标记提取 → 失败回退策略（b）目录扫描。

    返回路径基准：相对 workspace 根目录（如 ``sdlc/<sid>/code/lru.py``），
    与 §6.3 e2e 检查的 ``workspace/sdlc/<sid>/code/`` 物理路径一致。

    注：本函数依赖 ``sdlc_workspace.WORKSPACE_SDLC_DIR``，调用方需 import。
    """
    from orchestrator.sdlc_workspace import WORKSPACE_SDLC_DIR  # noqa: PLC0415

    # (a) 强约定标记：MiniMax 在输出末尾写 [FILES_WRITTEN] path1, path2
    if FILES_WRITTEN_MARKER in implementation:
        idx = implementation.index(FILES_WRITTEN_MARKER)
        rest = implementation[idx + len(FILES_WRITTEN_MARKER):]
        line = rest.split("\n")[0].strip()
        paths = [p.strip() for p in line.split(",") if p.strip()]
        if paths:
            # 归一化：确保都以 sdlc/<sid>/code/ 开头（MiniMax 可能写 code/lru.py 或完整路径）
            normalized = [_normalize_code_path(p, session_id) for p in paths]
            return [p for p in normalized if p]
    # (b) 兜底：扫描 workspace/sdlc/<sid>/code/ 目录
    code_dir = WORKSPACE_SDLC_DIR / session_id / "code"
    if code_dir.exists():
        # relative_to(WORKSPACE_SDLC_DIR.parent) → workspace 根，即 sdlc/<sid>/code/xxx
        return [
            str(p.relative_to(WORKSPACE_SDLC_DIR.parent))
            for p in code_dir.rglob("*")
            if p.is_file()
        ]
    return []


def _normalize_code_path(p: str, session_id: str) -> str | None:
    """把 MiniMax 写的路径归一化为 sdlc/<sid>/code/ 前缀。"""
    p = p.strip().strip("`")
    if not p:
        return None
    prefix = f"sdlc/{session_id}/code/"
    if p.startswith(prefix) or p.startswith(f"/{prefix}"):
        return p.lstrip("/")
    if p.startswith("code/"):
        return prefix + p[len("code/"):]
    return prefix + p.lstrip("/")
```

### 4.5 workspace 落盘策略（`sdlc_workspace.py`）

**双轨落盘**：

| 产物 | 落盘方式 | 说明 |
|---|---|---|
| `spec.md` / `tech-design.md` | orchestrator 直接写本地文件系统 | 不依赖 MCP，简单可靠 |
| `code/*` | MiniMax 通过 filesystem MCP 自己写 | 复用 MiniMax 已有的全权限 filesystem toolset；与"程序员自己写代码"角色一致 |

`workspace/` 在 docker-compose 里已挂载到所有容器（P3 已有），orchestrator 和 minimax-agent 共享同一卷。

```python
# orchestrator/sdlc_workspace.py

WORKSPACE_SDLC_DIR = Path(os.getenv("WORKSPACE_DIR", "workspace")) / "sdlc"


def write_sdlc_doc(session_id: str, filename: str, content: str) -> Path:
    """写 spec.md / tech-design.md 等。返回路径。"""
    target_dir = WORKSPACE_SDLC_DIR / session_id
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    path.write_text(content, encoding="utf-8")
    return path
```

### 4.6 节点 trace 与日志

所有 5 个节点都加 `@trace_node(name="orchestrator.sdlc.<node>")`，与现有 executor 节点一致。每次调用记 `session_id` / `feedback_rounds`，便于 Langfuse 追踪反馈回路。

---

## 5. Classifier WORKFLOW 路由 + 主图集成

### 5.1 WORKFLOW 关键词识别

核心思路：识别"端到端研发任务"语义（从需求到代码），区别于 DIRECT（单点问答）和 DECOMPOSITION（多视角对比）。

```python
# orchestrator/classifier.py 新增

WORKFLOW_KEYWORDS: tuple[str, ...] = (
    # 端到端研发语义
    "实现一个需求", "做一个功能", "做一个 feature", "开发一个",
    "从需求到代码", "完整实现", "端到端",
    # 显式工作流语义
    "走工作流", "走流程", "sdlc", "研发流程",
    # 交付物语义（暗示要 spec + 设计 + 代码）
    "交付一个", "产出一个", "帮我做一个",
)

# 复合模式：正则约束，避免"实现一个想法"误判
WORKFLOW_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "实现/开发/做一个 <名词>" 且 query 足够长（≥ 15 字，过滤短句）
    re.compile(r"(实现|开发|做一个|帮我做一个|交付).{2,30}"),
    # 显式 "完整" + 研发动词
    re.compile(r"完整.{0,10}(实现|开发|设计|流程)"),
)
```

### 5.2 classify 节点改造（优先级顺序很关键）

判定顺序必须**先 WORKFLOW 后 DIRECT**，否则"实现一个 X"会被 MINIMAX_KEYWORDS 的"实现一个"抢走路由到 MiniMax 单 Agent。

```python
@trace_node(name="orchestrator.classify")
def classify(state: OrchestrationState) -> dict[str, Any]:
    query = state["user_query"]
    log = logger.bind(...)

    # 1) TASK_DECOMPOSITION（最高优先级，正则强匹配）—— 不动
    if _is_decomposition(query):
        ...

    # 2) 【新增】WORKFLOW（次高优先级，关键词 + 正则 + 长度约束）
    if _is_workflow(query):
        log.info("classify_workflow", reason="workflow_keyword_or_pattern")
        return {
            "mode": OrchestrationMode.WORKFLOW.value,
            "target_agent": "",      # 不使用
            "subtasks": [],          # 不使用
            "workflow_status": "running",
            "feedback_rounds": 0,
        }

    # 3) DIRECT（最低优先级，兜底）—— 不动
    target, reason = _select_agent_by_keyword(query)
    ...


def _is_workflow(query: str) -> bool:
    """判断是否进入 SDLC WORKFLOW 模式。"""
    # 强模式：正则匹配（精准）
    for pattern in WORKFLOW_PATTERNS:
        if pattern.search(query):
            return True
    # 弱模式：关键词命中（需 query 足够长，避免短句误判）
    if len(query) >= 15:
        for kw in WORKFLOW_KEYWORDS:
            if kw in query:
                return True
    return False
```

### 5.3 判定优先级总表

| 优先级 | 模式 | 触发条件 | 典型 query |
|---|---|---|---|
| 1（最高） | DECOMPOSITION | 正则强匹配"对比/比较/分别" | "对比 Python 和 Go" |
| 2 | **WORKFLOW**（新） | 关键词 + 正则 + 长度约束 | "实现一个线程安全的 LRU 缓存" |
| 3（最低，兜底） | DIRECT | 关键词选 Agent，全 miss → GLM | "你好"、"写一个 hello 函数" |

### 5.4 主图集成（`graph.py` 改造）

```python
# orchestrator/graph.py

def build_graph() -> Any:
    graph = StateGraph(OrchestrationState)

    # 1) 节点（新增 workflow_execute）
    graph.add_node("classify", classify)
    graph.add_node("direct_execute", direct_execute)
    graph.add_node("decompose_execute", decompose_execute)
    graph.add_node("workflow_execute", workflow_execute)   # 新增
    graph.add_node("aggregate", aggregate)

    # 2) 边
    graph.add_edge(START, "classify")

    graph.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            "direct": "direct_execute",
            "decompose": "decompose_execute",
            "workflow": "workflow_execute",     # 新增
        },
    )

    graph.add_edge("direct_execute", "aggregate")
    graph.add_edge("decompose_execute", "aggregate")
    graph.add_edge("workflow_execute", "aggregate")    # 新增

    graph.add_edge("aggregate", END)
    return graph.compile()


def route_after_classify(state: OrchestrationState) -> str:
    mode = state.get("mode", "")
    if mode == OrchestrationMode.TASK_DECOMPOSITION.value:
        return "decompose"
    if mode == OrchestrationMode.WORKFLOW.value:       # 新增
        return "workflow"
    return "direct"
```

### 5.5 `workflow_execute` 节点实现

主图节点只做"子图调度 + 结果回填"，SDLC 业务逻辑全在 `sdlc_workflow.py`：

```python
# orchestrator/sdlc_workflow.py

def build_sdlc_graph() -> Any:
    """构建 SDLC 子图（独立 compile，便于单元测试）。"""
    sub = StateGraph(OrchestrationState)
    sub.add_node("deepseek_doc", deepseek_doc)
    sub.add_node("glm_spec", glm_spec)
    sub.add_node("minimax_code", minimax_code)
    sub.add_node("glm_feedback", glm_feedback)

    sub.add_edge(START, "deepseek_doc")
    sub.add_edge("deepseek_doc", "glm_spec")
    sub.add_edge("glm_spec", "minimax_code")

    sub.add_conditional_edges(
        "minimax_code",
        check_blocked,                       # 返回 "glm_feedback" 或 "done"
        {
            "glm_feedback": "glm_feedback",
            "done": END,
        },
    )

    sub.add_edge("glm_feedback", "minimax_code")  # 形成回路

    return sub.compile()


_COMPILED_SDLC_GRAPH: Any = None

def get_compiled_sdlc_graph() -> Any:
    global _COMPILED_SDLC_GRAPH
    if _COMPILED_SDLC_GRAPH is None:
        _COMPILED_SDLC_GRAPH = build_sdlc_graph()
    return _COMPILED_SDLC_GRAPH


async def workflow_execute(state: OrchestrationState) -> dict[str, Any]:
    """主图节点：跑 SDLC 子图，返回 partial state。"""
    sub = get_compiled_sdlc_graph()
    result = await asyncio.wait_for(sub.ainvoke(state), timeout=SDLC_TIMEOUT_SECONDS)
    return dict(result)
```

关键设计点：
1. 子图共享主图 state schema（都用 `OrchestrationState`），子图直接读写 `sdlc_doc` / `sdlc_feedback` / `feedback_rounds`，无需字段映射
2. 子图独立 compile，可在契约测试里单独 `build_sdlc_graph()` 并 mock Agent 调用测回路逻辑
3. `SDLC_TIMEOUT_SECONDS=600`（10 分钟）：工作流最多 5+ 次 LLM 调用（DeepSeek + GLM + MiniMax×最多3 + GLM 反馈×最多2），每次 ~60s 上限

### 5.6 aggregate 改造

`aggregate` 节点需识别 WORKFLOW 模式，把各阶段产出按研发流程顺序拼接：

```python
# orchestrator/aggregator.py

def aggregate(state: OrchestrationState) -> dict[str, Any]:
    mode = state.get("mode", "")

    # 【新增】WORKFLOW 模式：按研发流程顺序拼接
    if mode == OrchestrationMode.WORKFLOW.value:
        return _aggregate_workflow(state)

    # 现有 DIRECT / DECOMPOSITION 逻辑不动
    ...


def _aggregate_workflow(state: OrchestrationState) -> dict[str, Any]:
    """WORKFLOW 模式专属聚合。"""
    doc: SdlcDoc = state.get("sdlc_doc", {})
    feedbacks: list[SdlcFeedback] = state.get("sdlc_feedback", [])
    errors: list[str] = state.get("errors", [])
    rounds = state.get("feedback_rounds", 0)

    sections: list[str] = []

    # 1. Spec（DeepSeek）
    if doc.get("spec"):
        sections.append(f"## 📋 Spec（DeepSeek 产 PRD）\n\n{doc['spec']}")

    # 2. 技术规范（GLM-5.2）
    if doc.get("tech_design"):
        sections.append(f"## 🏗️ 技术规范（GLM-5.2）\n\n{doc['tech_design']}")

    # 3. 反馈回路记录（如有）
    for fb in feedbacks:
        sections.append(
            f"## 🔁 反馈轮次 {fb['round']}（GLM-5.2 → MiniMax）\n\n"
            f"**MiniMax 阻塞**：{fb['blocker']}\n\n"
            f"**GLM 指导**：{fb['guidance']}"
        )

    # 4. 实现（MiniMax）
    if doc.get("implementation"):
        status_emoji = (
            "✅" if rounds == 0 or NEED_HELP_MARKER not in doc["implementation"] else "⚠️"
        )
        sections.append(f"## {status_emoji} 实现（MiniMax）\n\n{doc['implementation']}")

    # 5. 产出文件清单
    if doc.get("code_paths"):
        paths = "\n".join(f"- `{p}`" for p in doc["code_paths"])
        sections.append(f"## 📁 落盘文件\n\n{paths}")

    # 6. 错误附录
    if errors:
        sections.append("## ⚠️ 执行错误\n\n" + "\n".join(f"- {e}" for e in errors))

    # 7. 状态总结
    status = state.get("workflow_status", "running")
    if NEED_HELP_MARKER in doc.get("implementation", "") and rounds >= MAX_FEEDBACK_ROUNDS:
        status_summary = (
            f"⚠️ MiniMax 仍有阻塞但已达反馈上限（{rounds}/{MAX_FEEDBACK_ROUNDS}），需人工介入"
        )
        status = "blocked_unresolved"
    elif rounds > 0:
        status_summary = f"✅ 经 {rounds} 轮反馈后完成"
        status = "blocked_resolved"
    else:
        status_summary = "✅ 一次通过，无反馈"
        status = "blocked_resolved"

    sections.append(f"## 📊 工作流状态\n\n{status_summary}")

    final = "\n\n---\n\n".join(sections)
    return {"final_answer": final, "workflow_status": status}
```

---

## 6. 测试策略

### 6.1 测试金字塔

```
            ┌─────────────────────┐
            │   e2e（P2.1 新增）   │  依赖 docker 栈 + 真实 API Key
            ├─────────────────────┤
            │ contract（P2.1 新增）│  纯 Python，mock Agent，无网络
            ├─────────────────────┤
            │  现有 contract/e2e   │  P1~P5 不动（部分同步更新）
            └─────────────────────┘
```

### 6.2 契约测试（`tests/contract/test_sdlc_workflow.py`）

9 个测试，纯 Python，mock Agent 调用，无网络无 docker，秒级完成。

| # | 测试 | 验证点 |
|---|---|---|
| 1 | `test_sdlc_graph_structure` | 子图含 4 节点 + 正确边 + 条件分支 |
| 2 | `test_sdlc_happy_path` | 无 [NEED_HELP] → 一次通过，feedback_rounds=0 |
| 3 | `test_sdlc_one_feedback_resolved` | 第 1 次遇阻 → GLM 反馈 → 第 2 次通过 |
| 4 | `test_sdlc_max_rounds_exhausted` | 连续遇阻 → 卡在 MAX_FEEDBACK_ROUNDS |
| 5 | `test_strip_oversized_code_from_glm` | GLM 超长代码块被剥离 + 短块保留 |
| 6 | `test_detect_minimax_freestyle` | 自由发挥语义被检测 + 合规输出不误报 |
| 7 | `test_classify_workflow_*`（3 子用例） | 长句触发 / 短句不触发 / DECOMP 优先 |
| 8 | `test_write_sdlc_doc` | spec.md/tech-design.md 落盘正确 |
| 9 | `test_main_graph_has_workflow_branch` + `test_route_after_classify_workflow` | 主图集成正确 |

测试骨架（完整版在实施阶段编写）：

```python
@pytest.mark.asyncio
async def test_sdlc_happy_path(monkeypatch):
    """MiniMax 一次通过（无 [NEED_HELP]）→ 不进 glm_feedback → aggregate。"""
    async def fake_send(url, text, **kw):
        if "deepseek" in url: return "# Spec\n实现 LRU 缓存"
        if "glm" in url: return "# 技术规范\n文件: lru.py"
        if "minimax" in url: return "已实现 lru.py\npytest 全过\n[FILES_WRITTEN] code/lru.py"
    monkeypatch.setattr(sdlc_workflow.a2a_client, "message_send", fake_send)

    result = await run_sdlc_subgraph(_make_state("实现一个 LRU 缓存"))

    assert result["feedback_rounds"] == 0
    assert result["sdlc_feedback"] == []
    assert "spec" in result["sdlc_doc"]
    assert "tech_design" in result["sdlc_doc"]
    assert "implementation" in result["sdlc_doc"]


def test_detect_minimax_freestyle():
    """MiniMax 输出含自由发挥语义 → 视同遇阻。"""
    freestyle_outputs = [
        "我额外加了一个 cache_size 参数",
        "我觉得应该用 Redis 替代内存缓存",
        "我建议增加一个 TTL 功能",
    ]
    for txt in freestyle_outputs:
        assert _detect_freestyle(txt) is not None


def test_detect_minimax_compliant():
    """严格按规范实现的输出 → 不触发自由发挥检测。"""
    compliant = "已按技术规范实现 lru.py，导出 LRUCache 类，附 5 个 pytest 用例，全过"
    assert _detect_freestyle(compliant) is None


def test_classify_workflow_keyword():
    """'实现一个 X'（≥15 字）→ WORKFLOW。"""
    state = _make_state("实现一个线程安全的 LRU 缓存并附测试")
    out = classify(state)
    assert out["mode"] == OrchestrationMode.WORKFLOW.value


def test_classify_workflow_not_triggered_for_short():
    """短句'实现一个 X'（<15 字）→ 不触发 WORKFLOW，走 DIRECT。"""
    state = _make_state("实现一个 hello")
    out = classify(state)
    assert out["mode"] != OrchestrationMode.WORKFLOW.value


def test_classify_decomposition_overrides_workflow():
    """对比类问题优先走 DECOMPOSITION（即使含'实现'）。"""
    state = _make_state("对比 Python 和 Go 实现一个 web 服务的区别")
    out = classify(state)
    assert out["mode"] == OrchestrationMode.TASK_DECOMPOSITION.value
```

### 6.3 e2e 测试（`tests/test_p2_1_e2e.py`）

仿照 `test_p2_e2e.py` 风格，新增 `p2_1_e2e` marker。**断言只验证"协议/结构/落盘"，不验证 LLM 答案内容**。

前置条件（`conftest.py`）：`pytest -m "p2_1_e2e"` 时探活 litellm + 3 Agent + orchestrator 全 healthy。

| # | 测试 | 验证点 |
|---|---|---|
| 1 | `test_workflow_e2e_happy_path` | 端到端：路由 / state 字段齐全 / 角色边界 / 落盘 / 答案含阶段标题 / 反馈轮数合规 |
| 2 | `test_workflow_e2e_trace_endpoint` | `/v1/orchestrate/trace` 返回完整 state |
| 3 | `test_workflow_e2e_workspace_files_persisted` | 落盘文件持久化 |

测试骨架：

```python
@pytest.mark.e2e
@pytest.mark.p2_1_e2e
def test_workflow_e2e_happy_path(orchestrator_url):
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate",
        json={"query": "实现一个线程安全的 LRU 缓存，要求支持 TTL 过期，并附完整的 pytest 单元测试"},
        timeout=600.0,
    )
    assert response.status_code == 200
    body = response.json()

    # 1. 路由正确
    assert body["mode"] == "workflow"

    # 2. State 字段齐全
    assert "sdlc_doc" in body["full_state"]
    doc = body["full_state"]["sdlc_doc"]
    assert doc.get("spec")
    assert doc.get("tech_design")
    assert doc.get("implementation")

    # 3. 角色边界（GLM 不写实现代码）
    _assert_glm_no_implementation(doc["tech_design"])

    # 4. MiniMax 严格遵循（无自由发挥语义）
    _assert_no_freestyle_in_final(doc["implementation"])

    # 5. 落盘文件存在
    sid = body["session_id"]
    workspace_dir = Path("workspace") / "sdlc" / sid
    assert (workspace_dir / "spec.md").exists()
    assert (workspace_dir / "tech-design.md").exists()
    code_files = list((workspace_dir / "code").glob("**/*")) if (workspace_dir / "code").exists() else []
    assert len(code_files) >= 1, "MiniMax 未落盘任何代码文件"
    # code_paths（state 内）基准是相对 workspace 根，即 sdlc/<sid>/code/xxx
    code_paths = doc.get("code_paths", [])
    assert all(p.startswith(f"sdlc/{sid}/code/") for p in code_paths), (
        f"code_paths 基准不一致: {code_paths}"
    )

    # 6. 最终答案含研发流程各阶段标题
    assert "Spec" in body["answer"] or "📋" in body["answer"]
    assert "技术规范" in body["answer"] or "🏗️" in body["answer"]
    assert "实现" in body["answer"] or "✅" in body["answer"]

    # 7. 反馈轮数合规
    assert body["full_state"].get("feedback_rounds", 0) <= MAX_FEEDBACK_ROUNDS


def _assert_glm_no_implementation(tech_design: str):
    """GLM 技术规范不应含完整实现（允许类型签名/伪代码）。"""
    for block in re.findall(r"```python\n(.*?)\n```", tech_design, re.DOTALL):
        lines = [l for l in block.split("\n") if l.strip() and not l.strip().startswith("#")]
        if len(lines) > GLM_CODE_BLOCK_MAX_LINES:
            pytest.fail(
                f"GLM tech_design 含超长代码块（{len(lines)} 行），疑似完整实现:\n{block[:200]}"
            )


def _assert_no_freestyle_in_final(implementation: str):
    """MiniMax 最终实现不应含自由发挥语义。"""
    freestyle_markers = ["我额外", "我觉得应该", "我建议增加", "我自己加"]
    for marker in freestyle_markers:
        if marker in implementation:
            pytest.fail(f"MiniMax 最终实现含自由发挥语义 '{marker}'，未被反馈环节纠正")
```

### 6.4 现有测试影响分析

| 现有测试 | 影响 | 说明 |
|---|---|---|
| `test_p1_e2e.py` | ✅ 不受影响 | 不走 orchestrator |
| `test_p2_e2e.py` | ✅ 不受影响 | 测试用 query（"你好"、"对比 X 和 Y"）不触发 WORKFLOW |
| `test_p3_e2e.py` | ✅ 不受影响 | MCP 测试 |
| `test_p4_e2e.py` | ✅ 不受影响 | Langfuse 测试 |
| `test_p5_e2e.py` | ✅ 不受影响 | OpenAI 兼容层；`model=auto` 走 DIRECT |
| `tests/contract/test_classifier.py` | ⚠️ 需更新 | 现有"实现一个 X → minimax"断言可能被 WORKFLOW 抢路由 |
| `tests/contract/test_graph.py` | ⚠️ 需更新 | 主图结构变化（新增 workflow_execute 节点） |
| `tests/contract/test_aggregator.py` | ⚠️ 需更新 | aggregate 新增 WORKFLOW 分支 |

关键：classifier 关键词优先级变了（"实现一个" 长句现在优先走 WORKFLOW 而非 DIRECT→MiniMax）。现有断言需同步更新（短句仍走 DIRECT；长句改触发 WORKFLOW）。

### 6.5 测试执行方式

```powershell
# 仅契约测试（无 docker，秒级）
pytest tests/contract/test_sdlc_workflow.py -v

# 仅 P2.1 e2e（需 docker 栈 + API Key）
pytest -m "p2_1_e2e" tests/test_p2_1_e2e.py -v

# 全跑
pytest -m "e2e"
```

---

## 7. 实施步骤总览

按依赖顺序，每步都是一个**可独立提交 + 可独立测试**的增量。

| # | 步骤 | 改动文件 | 测试 | 依赖 |
|---|---|---|---|---|
| 1 | State schema 扩展 | `orchestrator/state.py` | 契约：import + 字段存在 | 无 |
| 2 | Prompt 模板 | `orchestrator/sdlc_prompts.py`（新） | 契约：模板含 `{user_query}` 占位 | 1 |
| 3 | Workspace 落盘辅助 | `orchestrator/sdlc_workspace.py`（新） | 契约：`test_write_sdlc_doc` | 1 |
| 4 | 节点实现（5 个 + 兜底校验工具） | `orchestrator/sdlc_workflow.py`（新） | 契约：strip/freestyle/extract 单测 | 1,2,3 |
| 5 | SDLC 子图组装 | `orchestrator/sdlc_workflow.py`（续） | 契约：`test_sdlc_graph_structure` | 4 |
| 6 | 子图回路测试（mock Agent） | `tests/contract/test_sdlc_workflow.py`（新） | 契约：happy/feedback/max_rounds | 5 |
| 7 | classifier WORKFLOW 路由 | `orchestrator/classifier.py` | 契约：`test_classify_workflow_*` | 1 |
| 8 | 主图集成 + aggregate 改造 | `orchestrator/graph.py` `orchestrator/aggregator.py` | 契约：主图分支 + aggregator | 5,7 |
| 9 | 现有 contract 测试更新 | `tests/contract/test_classifier.py` `test_graph.py` `test_aggregator.py` | 回归绿 | 7,8 |
| 10 | e2e 测试 | `tests/test_p2_1_e2e.py`（新）+ `conftest.py` 加 marker | 需 docker 栈 | 1~9 |
| 11 | 文档同步 | `SPEC.md` `ARCHITECTURE.md` `DECISIONS.md` `NORTH_STAR.md` | 人工 review | 全部 |

关键里程碑：
- **步骤 1-6 完成后**：可独立跑契约测试，验证工作流逻辑正确（不需 docker）
- **步骤 7-9 完成后**：全 contract 绿 + 现有 e2e 无回归
- **步骤 10 完成后**：完整 e2e 跑通（需 docker + API Key）
- **步骤 11**：文档闭环

---

## 8. 文件清单

### 8.1 新增文件（7 个）

| 文件 | 行数估算 | 职责 |
|---|---|---|
| `orchestrator/sdlc_prompts.py` | ~150 | 4 段 prompt 模板 |
| `orchestrator/sdlc_workspace.py` | ~60 | workspace 落盘辅助 |
| `orchestrator/sdlc_workflow.py` | ~280 | 5 节点 + 兜底校验 + 子图组装 |
| `tests/contract/test_sdlc_workflow.py` | ~350 | 9 个契约测试 |
| `tests/test_p2_1_e2e.py` | ~180 | 3 个 e2e 测试 |
| `docs/superpowers/specs/2026-06-17-sdlc-workflow-design.md` | ~600 | 本设计文档 |
| `docs/DECISIONS.md`（追加 ADR-0009） | ~80 | 架构决策记录 |

### 8.2 修改文件（10 个）

| 文件 | 改动量 | 改动点 |
|---|---|---|
| `orchestrator/state.py` | +30 行 | `OrchestrationMode.WORKFLOW` + `SdlcDoc`/`SdlcFeedback` TypedDict + state 字段 |
| `orchestrator/classifier.py` | +60 行 | `WORKFLOW_KEYWORDS`/`WORKFLOW_PATTERNS` + `_is_workflow` + classify 优先级 |
| `orchestrator/graph.py` | +15 行 | `workflow_execute` 节点 + 路由分支 + edge |
| `orchestrator/aggregator.py` | +80 行 | `_aggregate_workflow` 函数 |
| `tests/conftest.py` | +10 行 | `p2_1_e2e` marker 探活逻辑 |
| `tests/contract/test_classifier.py` | ±20 行 | 更新"实现一个 X"长句走 WORKFLOW 的断言 |
| `tests/contract/test_graph.py` | +10 行 | 主图含 workflow_execute 节点 |
| `tests/contract/test_aggregator.py` | +40 行 | WORKFLOW 聚合分支测试 |
| `pyproject.toml` | +1 行 | `p2_1_e2e` marker 声明 |
| `agents/{glm,deepseek,minimax}_agent/agent.py` | 各 +10 行 | `default_instruction` 加 SDLC 工作流角色边界提示（第二道防线，可后置步骤 11） |

---

## 9. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| **LLM 不遵守角色约束**（GLM 偷写代码 / MiniMax 自由发挥） | 高 | 中 | 三层防御：prompt 硬约束 + 节点兜底校验 + Agent instruction；e2e 启发式检测 |
| **反馈回路死循环**（条件边写错） | 中 | 高 | `MAX_FEEDBACK_ROUNDS` 硬上限 + 契约测试 `test_sdlc_max_rounds_exhausted` 专门验证 |
| **MiniMax 不通过 MCP 真落盘**（LLM 只在文本里贴代码） | 高 | 中 | `[FILES_WRITTEN]` 约定标记 + 目录扫描兜底 + e2e 断言文件存在 |
| **classifier 误判**（短句触发 WORKFLOW / 长句漏判） | 中 | 低 | 长度门槛 15 字 + 正则约束 + 契约测试覆盖；保留 DIRECT 兜底 |
| **e2e 耗时长**（5+ 次 LLM 调用 × 60s） | 高 | 低 | `SDLC_TIMEOUT_SECONDS=600`；e2e 用 `pytest -m p2_1_e2e` 独立跑 |
| **子图共享主图 state 的 LangGraph 兼容性** | 低 | 高 | 步骤 5 子图组装后立即跑契约测试验证；退路见 §10 |
| **三家模型 API 不稳定 / 限流** | 中 | 中 | LiteLLM 已配 `retry:3` + `num_retries:2`；conftest 层失败重试 |
| **现有 contract 测试回归** | 中 | 低 | 步骤 9 专门处理；优先级 DECOMP > WORKFLOW > DIRECT 保证短句行为不变 |
| **workspace 卷在 docker 外路径不一致** | 低 | 中 | `WORKSPACE_DIR` env 可覆盖；e2e 用相对路径 |

---

## 10. 退路（Fallback）

如果某步卡住（按 NORTH_STAR §7 失败回退策略）：

1. **LangGraph 子图共享 state 不兼容** → 退路：把 SDLC 节点直接铺到主图（不抽子图），主图变 7 节点，复杂度上升但可用。记录到 DECISIONS.md。
2. **LLM 角色约束完全失效**（三家都不听 prompt） → 退路：在节点级做强后处理（regex 剥离 / 重写），记录到 DECISIONS.md。
3. **MiniMax MCP 落盘不稳定** → 退路：orchestrator 在 minimax_code 节点解析输出后代为落盘 code/*（违反"程序员自己写"角色但保证可用）。
4. **e2e 全跑超时** → 退路：把 e2e 拆成"单节点验证"（只跑 deepseek_doc / glm_spec 各一次），不做完整回路 e2e。

---

## 11. 相关决策记录（待写）

实施阶段在 `docs/DECISIONS.md` 追加 **ADR-0009：实装 SDLC WORKFLOW 编排模式**，记录：
- 选定方案 A（静态 StateGraph 子图）而非 B（动态引擎）/ C（手写协程）的理由
- `MAX_FEEDBACK_ROUNDS = 2` 的取舍（单轮反馈 vs 多轮循环）
- "节点自 merge 返回完整值"而非引入自定义 reducer 的理由
- 双轨落盘（orchestrator 写文档 / MiniMax 通过 MCP 写代码）的设计权衡

---

**最后更新**：2026-06-17
**状态**：设计已确认（§1~§7 全部通过），待进入实施计划阶段
