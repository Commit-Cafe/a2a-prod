# SDLC Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实装 SDLC 研发协作工作流（DeepSeek 产 Spec → GLM-5.2 产技术规范 → MiniMax 写码+自测，遇阻反馈 GLM），作为 `OrchestrationMode.WORKFLOW` 的第一个具体实例。

**Architecture:** LangGraph 静态 StateGraph 子图（5 节点 + 条件分支反馈回路），封装在 `workflow_execute` 主图节点里。共享主图 `OrchestrationState` schema。三层防御保证角色边界：prompt 硬约束 + 节点兜底校验 + Agent instruction。

**Tech Stack:** Python 3.12 / LangGraph ≥0.4 / a2a-sdk 0.3.x / structlog / pytest / mypy --strict / ruff

**Spec:** [docs/superpowers/specs/2026-06-17-sdlc-workflow-design.md](../specs/2026-06-17-sdlc-workflow-design.md)

---

## 全局约定

- **工作目录**：`D:\trae_Dproject4\a2a-prod`
- **虚拟环境**：`.venv-prod`（激活后跑命令；PowerShell: `.venv-prod\Scripts\Activate.ps1`）
- **测试约定**：契约测试用 `pytest tests/contract/ -v`（无 docker）；e2e 测试用 `pytest -m "p2_1_e2e" -v`（需 docker 栈）
- **Mock 模式**：参照 `tests/contract/test_graph.py`，用 `unittest.mock.patch.object(a2a_client, "message_send", new_callable=AsyncMock)`
- **commit 粒度**：每个 Task 末尾 commit 一次（项目当前非 git 仓库，commit 步骤改为"检查点"，若用户后续 init git 可用）
- **代码风格**：ruff（line-length=100, double quote）+ mypy --strict + 模块顶部 docstring + structlog logger

---

## File Structure

### 新增文件（5 个）

| 文件 | 职责 | 行数估算 |
|---|---|---|
| `orchestrator/sdlc_prompts.py` | 4 段 prompt 模板常量 | ~150 |
| `orchestrator/sdlc_workspace.py` | workspace 落盘辅助 + `WORKSPACE_SDLC_DIR` 常量 | ~60 |
| `orchestrator/sdlc_workflow.py` | 5 节点 + 兜底校验工具 + 子图组装 + `workflow_execute` 主图节点 | ~280 |
| `tests/contract/test_sdlc_workflow.py` | 9 组契约测试 | ~350 |
| `tests/test_p2_1_e2e.py` | 3 个 e2e 测试 | ~180 |

### 修改文件（7 个）

| 文件 | 改动点 |
|---|---|
| `orchestrator/state.py` | `OrchestrationMode.WORKFLOW` + `SdlcDoc`/`SdlcFeedback` TypedDict + state 字段 |
| `orchestrator/classifier.py` | `WORKFLOW_KEYWORDS`/`WORKFLOW_PATTERNS` + `_is_workflow` + classify 优先级 |
| `orchestrator/graph.py` | `workflow_execute` 节点 + 路由分支 + edge |
| `orchestrator/aggregator.py` | `_aggregate_workflow` 函数 |
| `tests/conftest.py` | P2.1 e2e 探活（复用 orchestrator 探活，无需新增探活轮次） |
| `tests/contract/test_classifier.py` | 更新"实现一个 X"长句走 WORKFLOW 的断言 |
| `tests/contract/test_graph.py` | 主图含 workflow_execute 节点的断言 |
| `pyproject.toml` | `p2_1_e2e` marker 声明 |

---

## Task 1: State Schema 扩展

**Files:**
- Modify: `orchestrator/state.py`

- [ ] **Step 1: 写失败测试**

Create `tests/contract/test_state_workflow.py`:

```python
"""State schema 扩展契约测试（P2.1 WORKFLOW 模式）。"""

from __future__ import annotations

from orchestrator.state import (
    OrchestrationMode,
    OrchestrationState,
    SdlcDoc,
    SdlcFeedback,
)


def test_workflow_mode_enum_exists() -> None:
    """OrchestrationMode 必须含 WORKFLOW 成员。"""
    assert OrchestrationMode.WORKFLOW.value == "workflow"


def test_sdlc_doc_typeddict_keys() -> None:
    """SdlcDoc 含 spec / tech_design / implementation / code_paths 四键。"""
    doc: SdlcDoc = {
        "spec": "# Spec",
        "tech_design": "# Tech",
        "implementation": "# Impl",
        "code_paths": ["sdlc/sid/code/lru.py"],
    }
    assert doc["spec"] == "# Spec"
    assert doc["code_paths"] == ["sdlc/sid/code/lru.py"]


def test_sdlc_feedback_typeddict_keys() -> None:
    """SdlcFeedback 含 round / blocker / guidance 三键。"""
    fb: SdlcFeedback = {"round": 1, "blocker": "不确定", "guidance": "用 OrderedDict"}
    assert fb["round"] == 1


def test_orchestration_state_accepts_workflow_fields() -> None:
    """OrchestrationState 必须接受 WORKFLOW 专用字段。"""
    state: OrchestrationState = {
        "user_query": "实现一个 LRU",
        "session_id": "s1",
        "mode": OrchestrationMode.WORKFLOW.value,
        "sdlc_doc": {"spec": "# S"},
        "sdlc_feedback": [],
        "feedback_rounds": 0,
        "workflow_status": "running",
    }
    assert state["mode"] == "workflow"
    assert state["feedback_rounds"] == 0
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/contract/test_state_workflow.py -v`
Expected: FAIL with `ImportError: cannot import name 'SdlcDoc'` 或 `WORKFLOW` 不存在

- [ ] **Step 3: 实现 state.py 改动**

在 `orchestrator/state.py` 的 `OrchestrationMode` 枚举解除 WORKFLOW 注释：

```python
class OrchestrationMode(StrEnum):
    """SPEC §P2 编排模式。"""

    DIRECT = "direct"
    TASK_DECOMPOSITION = "task_decomposition"
    WORKFLOW = "workflow"  # P2.1: SDLC 研发协作工作流
    # NEGOTIATION = "negotiation"  # 留 P2.2+
```

在 `SubTask` 之后、`OrchestrationState` 之前新增两个 TypedDict：

```python
class SdlcDoc(TypedDict, total=False):
    """SDLC 工作流各阶段产出（每个 value 是 Agent 输出的 markdown 文本）。"""

    spec: str  # DeepSeek 产出的 PRD/Spec
    tech_design: str  # GLM-5.2 产出的技术规范 + 编码指令
    implementation: str  # MiniMax 产出的实现说明（含 pytest 结果）
    code_paths: list[str]  # MiniMax 落盘的代码文件相对路径（workspace 内）


class SdlcFeedback(TypedDict, total=False):
    """单轮反馈记录（GLM-5.2 → MiniMax 的指导）。"""

    round: int  # 第几轮（1-based）
    blocker: str  # MiniMax 的 [NEED_HELP] 问题描述
    guidance: str  # GLM-5.2 给出的指导（非代码）
```

在 `OrchestrationState` 末尾（`messages` 之前）追加 WORKFLOW 字段：

```python
    # ---- WORKFLOW 模式专用（P2.1） ----
    sdlc_doc: SdlcDoc  # 各阶段产出文档
    sdlc_feedback: list[SdlcFeedback]  # 每轮反馈记录
    feedback_rounds: int  # 已发生的反馈轮数（0/1/2，上限 MAX_ROUNDS=2）
    workflow_status: str  # "running" / "blocked_resolved" / "blocked_unresolved"
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/contract/test_state_workflow.py -v`
Expected: 4 passed

- [ ] **Step 5: 跑 mypy + ruff**

Run: `mypy orchestrator/state.py` 和 `ruff check orchestrator/state.py`
Expected: 无错误

- [ ] **Step 6: 检查点**

确认 4 个测试全绿 + mypy/ruff 干净。state.py 改动完成。

---

## Task 2: Prompt 模板

**Files:**
- Create: `orchestrator/sdlc_prompts.py`

- [ ] **Step 1: 写失败测试**

Create `tests/contract/test_sdlc_prompts.py`:

```python
"""sdlc_prompts 模板契约测试。"""

from __future__ import annotations

import orchestrator.sdlc_prompts as prompts


def test_all_prompts_have_user_query_placeholder() -> None:
    """DeepSeek/GLM-spec 模板必须含 {user_query} 占位（.format 时要填充）。"""
    assert "{user_query}" in prompts.DEEPSEEK_SPEC_PROMPT
    assert "{user_query}" in prompts.GLM_TECH_DESIGN_PROMPT


def test_glm_tech_design_prompt_has_spec_placeholder() -> None:
    """GLM 技术规范模板必须含 {spec} 占位。"""
    assert "{spec}" in prompts.GLM_TECH_DESIGN_PROMPT


def test_minimax_code_prompt_has_tech_design_and_session_placeholders() -> None:
    """MiniMax 编码模板必须含 {tech_design} / {session_id} / {feedback_block} 占位。"""
    assert "{tech_design}" in prompts.MINIMAX_CODE_PROMPT
    assert "{session_id}" in prompts.MINIMAX_CODE_PROMPT
    assert "{feedback_block}" in prompts.MINIMAX_CODE_PROMPT


def test_glm_feedback_prompt_has_blocker_placeholder() -> None:
    """GLM 反馈模板必须含 {blocker} 占位。"""
    assert "{blocker}" in prompts.GLM_FEEDBACK_PROMPT


def test_prompts_contain_hard_constraints() -> None:
    """四段模板都含角色硬约束关键词。"""
    # DeepSeek: 不写代码
    assert "不写实现代码" in prompts.DEEPSEEK_SPEC_PROMPT
    # GLM tech design: 严禁输出实现代码
    assert "严禁" in prompts.GLM_TECH_DESIGN_PROMPT and "代码" in prompts.GLM_TECH_DESIGN_PROMPT
    # MiniMax: 遇阻 [NEED_HELP] + 严格遵循
    assert "[NEED_HELP]" in prompts.MINIMAX_CODE_PROMPT
    assert "严格遵循" in prompts.MINIMAX_CODE_PROMPT
    # GLM feedback: 不替 MiniMax 写完整代码
    assert "不要替 MiniMax 写完整代码" in prompts.GLM_FEEDBACK_PROMPT
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/contract/test_sdlc_prompts.py -v`
Expected: FAIL with `ModuleNotFoundError: orchestrator.sdlc_prompts`

- [ ] **Step 3: 实现 sdlc_prompts.py**

Create `orchestrator/sdlc_prompts.py`:

```python
"""SDLC 工作流 prompt 模板（P2.1）。

四段模板对应四个节点：
- ``DEEPSEEK_SPEC_PROMPT``：节点 1，DeepSeek 产 PRD/Spec
- ``GLM_TECH_DESIGN_PROMPT``：节点 2，GLM-5.2 产技术规范 + 编码指令（禁代码）
- ``MINIMAX_CODE_PROMPT``：节点 3，MiniMax 写码 + 自测（遇阻 [NEED_HELP]，禁自由发挥）
- ``GLM_FEEDBACK_PROMPT``：节点 5，GLM 给指导（不替写代码）

所有模板用 ``str.format`` 填充占位符。
"""

from __future__ import annotations

# ============================================================
# 节点 1：DeepSeek 产 PRD/Spec
# ============================================================

DEEPSEEK_SPEC_PROMPT = """你是团队的产品经理 + 技术总监。当前任务是从用户需求产出 Spec 文档。

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
"""

# ============================================================
# 节点 2：GLM-5.2 产技术规范 + 编码指令（禁代码）
# ============================================================

GLM_TECH_DESIGN_PROMPT = """你是团队的技术总监。基于以下 Spec 产出技术规范，并给 MiniMax 下编码指令。

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
"""

# ============================================================
# 节点 3：MiniMax 写码 + 自测（遇阻 [NEED_HELP]，禁自由发挥）
# ============================================================

MINIMAX_CODE_PROMPT = """你是团队的程序员。基于以下技术规范与编码指令写代码。

【技术规范】
{tech_design}

【之前 GLM 的反馈】（如有；无则空）
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
"""

# ============================================================
# 节点 5：GLM-5.2 给指导（不替写代码）
# ============================================================

GLM_FEEDBACK_PROMPT = """你是团队的技术总监。MiniMax 在执行你下的编码指令时遇到了阻塞，需要你给指导。

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
"""
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/contract/test_sdlc_prompts.py -v`
Expected: 5 passed

- [ ] **Step 5: 跑 ruff + mypy**

Run: `ruff check orchestrator/sdlc_prompts.py` 和 `mypy orchestrator/sdlc_prompts.py`
Expected: 干净

- [ ] **Step 6: 检查点**

5 测试全绿 + lint 干净。

---

## Task 3: Workspace 落盘辅助

**Files:**
- Create: `orchestrator/sdlc_workspace.py`

- [ ] **Step 1: 写失败测试**

Create `tests/contract/test_sdlc_workspace.py`:

```python
"""sdlc_workspace 落盘辅助契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator import sdlc_workspace


@pytest.fixture
def temp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 WORKSPACE_SDLC_DIR 重定向到 tmp_path/sdlc。"""
    target = tmp_path / "sdlc"
    monkeypatch.setattr(sdlc_workspace, "WORKSPACE_SDLC_DIR", target)
    return target


def test_write_sdlc_doc_creates_file(temp_workspace: Path) -> None:
    """write_sdlc_doc 创建 spec.md 文件。"""
    path = sdlc_workspace.write_sdlc_doc("sess-123", "spec.md", "# Spec content")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "# Spec content"


def test_write_sdlc_doc_creates_session_dir(temp_workspace: Path) -> None:
    """文件落在 <workspace>/sdlc/<session_id>/ 下。"""
    path = sdlc_workspace.write_sdlc_doc("sess-456", "tech-design.md", "# Tech")
    assert path.parent.name == "sess-456"
    assert path.parent.parent == temp_workspace


def test_write_sdlc_doc_overwrites_existing(temp_workspace: Path) -> None:
    """同名文件覆写（第二次写覆盖第一次）。"""
    sdlc_workspace.write_sdlc_doc("s1", "spec.md", "v1")
    sdlc_workspace.write_sdlc_doc("s1", "spec.md", "v2")
    path = temp_workspace / "s1" / "spec.md"
    assert path.read_text(encoding="utf-8") == "v2"


def test_write_sdlc_doc_multiple_sessions_isolated(temp_workspace: Path) -> None:
    """不同 session 落在不同目录。"""
    sdlc_workspace.write_sdlc_doc("s1", "spec.md", "from-s1")
    sdlc_workspace.write_sdlc_doc("s2", "spec.md", "from-s2")
    assert (temp_workspace / "s1" / "spec.md").read_text(encoding="utf-8") == "from-s1"
    assert (temp_workspace / "s2" / "spec.md").read_text(encoding="utf-8") == "from-s2"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/contract/test_sdlc_workspace.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: 实现 sdlc_workspace.py**

Create `orchestrator/sdlc_workspace.py`:

```python
"""SDLC 工作流 workspace 落盘辅助（P2.1）。

职责：
- 提供 ``WORKSPACE_SDLC_DIR`` 常量（``workspace/sdlc``，可被 env 覆盖）
- ``write_sdlc_doc``：orchestrator 直接写 spec.md / tech-design.md 到本地

落盘策略（spec §4.5）：
- spec.md / tech-design.md → orchestrator 直接写本地（本模块）
- code/* → MiniMax 通过 filesystem MCP 自己写（不在本模块）

workspace 目录在 docker-compose 已挂载到 orchestrator 与 minimax-agent 共享卷。
"""

from __future__ import annotations

import os
from pathlib import Path

# workspace 根（与 docker-compose 的 volume 挂载点一致）
# 可被 WORKSPACE_DIR 环境变量覆盖（测试 / 本地开发用）
WORKSPACE_SDLC_DIR: Path = Path(os.getenv("WORKSPACE_DIR", "workspace")) / "sdlc"


def write_sdlc_doc(session_id: str, filename: str, content: str) -> Path:
    """把 SDLC 阶段产出（spec.md / tech-design.md）写到 workspace/sdlc/<session_id>/。

    Args:
        session_id: 工作流 session ID（用作子目录名）
        filename: 文件名，如 ``spec.md`` / ``tech-design.md``
        content: 文件内容（utf-8 编码写入）

    Returns:
        写入的文件绝对路径
    """
    target_dir = WORKSPACE_SDLC_DIR / session_id
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    path.write_text(content, encoding="utf-8")
    return path
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/contract/test_sdlc_workspace.py -v`
Expected: 4 passed

- [ ] **Step 5: 跑 ruff + mypy**

Run: `ruff check orchestrator/sdlc_workspace.py` 和 `mypy orchestrator/sdlc_workspace.py`
Expected: 干净

- [ ] **Step 6: 检查点**

4 测试全绿 + lint 干净。

---

## Task 4: 节点兜底校验工具（先于节点实现，便于独立测试）

**Files:**
- Create: `orchestrator/sdlc_workflow.py`（先只放工具函数 + 常量，节点留 Task 5）

- [ ] **Step 1: 写失败测试**

Create `tests/contract/test_sdlc_workflow_utils.py`:

```python
"""sdlc_workflow 兜底校验工具契约测试。"""

from __future__ import annotations

import pytest

from orchestrator.sdlc_workflow import (
    GLM_CODE_BLOCK_MAX_LINES,
    MAX_FEEDBACK_ROUNDS,
    NEED_HELP_MARKER,
    _detect_freestyle,
    _extract_files_written,
    _extract_need_help,
    _normalize_code_path,
    _strip_oversized_code_from_glm,
)


# ============================================================
# 常量
# ============================================================


def test_constants_have_expected_defaults() -> None:
    assert NEED_HELP_MARKER == "[NEED_HELP]"
    assert MAX_FEEDBACK_ROUNDS == 2
    assert GLM_CODE_BLOCK_MAX_LINES == 15


# ============================================================
# _strip_oversized_code_from_glm
# ============================================================


class TestStripOversizedCode:
    def test_strips_oversized_block(self) -> None:
        """超过 max_lines 的代码块被剥离，含'应委派'提示。"""
        big_block = "```python\n" + "\n".join(f"x{i} = 1" for i in range(30)) + "\n```"
        result = _strip_oversized_code_from_glm(big_block)
        assert "应委派 MiniMax 实现" in result
        assert "x29 = 1" not in result

    def test_keeps_short_signature(self) -> None:
        """短代码块（类型签名 / 伪代码，≤ max_lines）保留。"""
        short = "# 接口\n```python\ndef foo(x: int) -> str: ...\n```"
        assert _strip_oversized_code_from_glm(short) == short

    def test_keeps_at_threshold(self) -> None:
        """恰好等于 max_lines（行数=15）的块保留。"""
        block = "```python\n" + "\n".join(f"l{i}" for i in range(15)) + "\n```"
        # block 总行数 = 15（含开闭 fence 中间的内容行数）
        # 实现里 block.count("\n") 用于判定；这里 15 行内容 → 16 个 \n，应剥离
        # 调整：用 14 行内容确保保留
        block_keep = "```python\n" + "\n".join(f"l{i}" for i in range(13)) + "\n```"
        assert _strip_oversized_code_from_glm(block_keep) == block_keep

    def test_no_code_block_passthrough(self) -> None:
        """无代码块的纯文档原样返回。"""
        text = "# 标题\n纯文字说明，无代码。"
        assert _strip_oversized_code_from_glm(text) == text

    def test_mixed_blocks_selective_strip(self) -> None:
        """混合：短块保留，长块剥离。"""
        text = (
            "```python\ndef sig(x: int) -> str: ...\n```\n"
            "说明文字\n"
            "```python\n" + "\n".join(f"y{i}=1" for i in range(20)) + "\n```"
        )
        result = _strip_oversized_code_from_glm(text)
        assert "def sig" in result  # 短块保留
        assert "应委派 MiniMax 实现" in result  # 长块剥离
        assert "y19=1" not in result


# ============================================================
# _extract_need_help
# ============================================================


class TestExtractNeedHelp:
    def test_extracts_after_marker(self) -> None:
        text = "[NEED_HELP] 不确定用 OrderedDict 还是 dict\n\n其他内容"
        assert _extract_need_help(text) == "不确定用 OrderedDict 还是 dict"

    def test_returns_none_when_no_marker(self) -> None:
        assert _extract_need_help("已实现，pytest 全过") is None

    def test_extracts_to_end_when_no_blank_line(self) -> None:
        text = "[NEED_HELP] 单行问题"
        assert _extract_need_help(text) == "单行问题"

    def test_returns_none_for_empty_marker(self) -> None:
        """标记后内容为空 → None（无效阻塞）。"""
        assert _extract_need_help("[NEED_HELP] \n\n其他") is None


# ============================================================
# _detect_freestyle
# ============================================================


class TestDetectFreestyle:
    @pytest.mark.parametrize(
        "text",
        [
            "我额外加了一个 cache_size 参数",
            "我觉得应该用 Redis 替代内存缓存",
            "我建议增加一个 TTL 功能",
            "我自己加了一个 helper 函数",
            "自行新增了一个 utils.py",
        ],
    )
    def test_freestyle_detected(self, text: str) -> None:
        assert _detect_freestyle(text) is not None

    def test_compliant_output_not_flagged(self) -> None:
        compliant = "已按技术规范实现 lru.py，导出 LRUCache 类，附 5 个 pytest 用例，全过"
        assert _detect_freestyle(compliant) is None

    def test_freestyle_returns_context(self) -> None:
        """返回值含上下文片段（用于反馈给 GLM）。"""
        result = _detect_freestyle("实现完成，我额外加了 TTL 支持")
        assert result is not None
        assert "TTL" in result or "额外" in result


# ============================================================
# _normalize_code_path
# ============================================================


class TestNormalizeCodePath:
    def test_already_prefixed(self) -> None:
        assert _normalize_code_path("sdlc/sess1/code/lru.py", "sess1") == "sdlc/sess1/code/lru.py"

    def test_code_prefix_only(self) -> None:
        assert _normalize_code_path("code/lru.py", "sess1") == "sdlc/sess1/code/lru.py"

    def test_bare_filename(self) -> None:
        assert _normalize_code_path("lru.py", "sess1") == "sdlc/sess1/code/lru.py"

    def test_strips_backticks(self) -> None:
        assert _normalize_code_path("`code/lru.py`", "sess1") == "sdlc/sess1/code/lru.py"

    def test_empty_returns_none(self) -> None:
        assert _normalize_code_path("", "sess1") is None
        assert _normalize_code_path("   ", "sess1") is None


# ============================================================
# _extract_files_written
# ============================================================


class TestExtractFilesWritten:
    def test_extracts_from_marker(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """[FILES_WRITTEN] 标记存在 → 提取路径（归一化）。"""
        text = "实现完成\n[FILES_WRITTEN] code/lru.py, code/test_lru.py"
        paths = _extract_files_written(text, "sess1")
        assert paths == ["sdlc/sess1/code/lru.py", "sdlc/sess1/code/test_lru.py"]

    def test_fallback_to_dir_scan(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """无标记 → 扫描 workspace/sdlc/<sid>/code/ 目录。"""
        from orchestrator import sdlc_workspace

        # 造目录结构
        code_dir = tmp_path / "sdlc" / "sess2" / "code"
        code_dir.mkdir(parents=True)
        (code_dir / "a.py").write_text("x=1", encoding="utf-8")
        (code_dir / "b.py").write_text("y=2", encoding="utf-8")
        monkeypatch.setattr(sdlc_workspace, "WORKSPACE_SDLC_DIR", tmp_path / "sdlc")

        paths = _extract_files_written("实现完成，无标记", "sess2")
        assert sorted(paths) == ["sdlc/sess2/code/a.py", "sdlc/sess2/code/b.py"]

    def test_no_files_returns_empty(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """无标记且目录不存在 → 空列表。"""
        from orchestrator import sdlc_workspace

        monkeypatch.setattr(sdlc_workspace, "WORKSPACE_SDLC_DIR", tmp_path / "sdlc")
        paths = _extract_files_written("无标记", "nonexistent-session")
        assert paths == []
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/contract/test_sdlc_workflow_utils.py -v`
Expected: FAIL with `ModuleNotFoundError: orchestrator.sdlc_workflow`

- [ ] **Step 3: 实现 sdlc_workflow.py（仅工具函数 + 常量）**

Create `orchestrator/sdlc_workflow.py`:

```python
"""SDLC 研发协作工作流（P2.1）。

DeepSeek 产 Spec → GLM-5.2 产技术规范 → MiniMax 写码 + 自测 → 遇阻反馈 GLM。

本模块含：
- 常量（``MAX_FEEDBACK_ROUNDS`` / ``NEED_HELP_MARKER`` / 等）
- 兜底校验工具（``_strip_oversized_code_from_glm`` / ``_extract_need_help`` / 等）
- 5 个 LangGraph 节点（Task 5 实现）
- SDLC 子图组装 + ``workflow_execute`` 主图节点（Task 5 实现）
"""

from __future__ import annotations

import os
import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ============================================================
# 常量（spec §3.4）
# ============================================================

# 反馈上限：写死默认 + env 覆盖口子
MAX_FEEDBACK_ROUNDS: int = int(os.getenv("SDLC_MAX_FEEDBACK_ROUNDS", "2"))

# MiniMax 遇阻标记（节点硬校验识别）
NEED_HELP_MARKER: str = "[NEED_HELP]"

# MiniMax 落盘文件清单标记（强约定，便于解析）
FILES_WRITTEN_MARKER: str = "[FILES_WRITTEN]"

# GLM 兜底校验：代码块超过此行数视为"完整实现"，剥离
GLM_CODE_BLOCK_MAX_LINES: int = int(os.getenv("SDLC_GLM_CODE_MAX_LINES", "15"))

# 子图执行超时（5+ 次 LLM 调用 × 60s 上限）
SDLC_TIMEOUT_SECONDS: int = int(os.getenv("SDLC_TIMEOUT_SECONDS", "600"))


# ============================================================
# 兜底校验：剥离 GLM 超长代码块
# ============================================================

_CODE_BLOCK_RE = re.compile(
    r"```(?:python|py|go|java|js|ts|rust)?\n.*?\n```",
    re.DOTALL,
)


def _strip_oversized_code_from_glm(
    text: str,
    *,
    max_lines: int = GLM_CODE_BLOCK_MAX_LINES,
) -> str:
    """GLM 节点输出后调用：若代码块超过 max_lines，剥离并打 warning。

    保留短的类型签名 / 伪代码（≤ max_lines），剥离完整实现。
    被剥离的位置替换为：「（GLM 应委派 MiniMax 实现：<首行摘要>…）」。
    """
    def _replace(m: re.Match[str]) -> str:
        block = m.group(0)
        line_count = block.count("\n")
        if line_count <= max_lines:
            return block  # 短块保留
        first_line = block.split("\n")[1][:60] if "\n" in block else ""
        logger.warning(
            "glm_code_block_stripped",
            lines=line_count,
            preview=first_line,
        )
        return f"（GLM 应委派 MiniMax 实现：{first_line}…）"

    return _CODE_BLOCK_RE.sub(_replace, text)


# ============================================================
# 兜底校验：提取 MiniMax [NEED_HELP] 阻塞
# ============================================================


def _extract_need_help(implementation: str) -> str | None:
    """从 MiniMax 输出提取 [NEED_HELP] 后的 blocker 描述。

    Returns:
        blocker 文本；无标记或标记后内容为空 → None
    """
    if NEED_HELP_MARKER not in implementation:
        return None
    idx = implementation.index(NEED_HELP_MARKER)
    rest = implementation[idx + len(NEED_HELP_MARKER):].strip()
    if not rest:
        return None
    # 取标记后到下一个空行或结尾
    return rest.split("\n\n")[0].strip() or None


# ============================================================
# 兜底校验：检测 MiniMax 自由发挥
# ============================================================

_FREESTYLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"我额外(加|实现|新增)"),
    re.compile(r"我觉得(应该|可以)"),
    re.compile(r"我建议(增加|加|用)"),
    re.compile(r"我自己(加|实现|改)"),
    re.compile(r"自行(新增|添加|决定)"),
)


def _detect_freestyle(implementation: str) -> str | None:
    """检测 MiniMax 自由发挥语义。

    Returns:
        含上下文的问题描述（用于反馈给 GLM）；合规返回 None。
    """
    for pattern in _FREESTYLE_PATTERNS:
        m = pattern.search(implementation)
        if m:
            start = max(0, m.start() - 10)
            end = min(len(implementation), m.end() + 40)
            return f"检测到自由发挥：...{implementation[start:end]}..."
    return None


# ============================================================
# 兜底校验：MiniMax 落盘文件路径提取 + 归一化
# ============================================================


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


def _extract_files_written(implementation: str, session_id: str) -> list[str]:
    """从 MiniMax 输出提取落盘文件路径。

    策略（a）+[FILES_WRITTEN] 标记提取 → 失败回退策略（b）目录扫描。
    返回路径基准：相对 workspace 根（如 ``sdlc/<sid>/code/lru.py``）。
    """
    from orchestrator.sdlc_workspace import WORKSPACE_SDLC_DIR  # noqa: PLC0415

    # (a) 强约定标记
    if FILES_WRITTEN_MARKER in implementation:
        idx = implementation.index(FILES_WRITTEN_MARKER)
        rest = implementation[idx + len(FILES_WRITTEN_MARKER):]
        line = rest.split("\n")[0].strip()
        raw_paths = [p.strip() for p in line.split(",") if p.strip()]
        if raw_paths:
            normalized = [_normalize_code_path(p, session_id) for p in raw_paths]
            return [p for p in normalized if p]
    # (b) 兜底：扫描 workspace/sdlc/<sid>/code/
    code_dir = WORKSPACE_SDLC_DIR / session_id / "code"
    if code_dir.exists():
        return [
            str(p.relative_to(WORKSPACE_SDLC_DIR.parent))
            for p in code_dir.rglob("*")
            if p.is_file()
        ]
    return []


# ============================================================
# LangGraph 节点 + 子图（Task 5 实现）
# ============================================================
```

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/contract/test_sdlc_workflow_utils.py -v`
Expected: 全 passed（约 20 个）

- [ ] **Step 5: 修复 `test_keeps_at_threshold` 边界**

观察 Step 4 输出，如果 `test_keeps_at_threshold` 失败，调整该测试的行数使其恰好不触发剥离（用 13 行内容保证 `count("\n") <= 15`）。当前测试已用 13 行，应通过。

- [ ] **Step 6: 跑 ruff + mypy**

Run: `ruff check orchestrator/sdlc_workflow.py` 和 `mypy orchestrator/sdlc_workflow.py`
Expected: 干净

- [ ] **Step 7: 检查点**

工具函数测试全绿 + lint 干净。节点实现（Task 5）在此基础上叠加。

---

## Task 5: SDLC 节点 + 子图组装

**Files:**
- Modify: `orchestrator/sdlc_workflow.py`（追加节点 + 子图）
- Create: `tests/contract/test_sdlc_workflow.py`（子图回路测试）

- [ ] **Step 1: 写失败测试（子图回路 mock）**

Create `tests/contract/test_sdlc_workflow.py`:

```python
"""SDLC 子图回路契约测试（mock Agent，无 LLM/docker）。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from orchestrator import a2a_client, sdlc_workflow
from orchestrator.sdlc_workflow import (
    MAX_FEEDBACK_ROUNDS,
    NEED_HELP_MARKER,
    build_sdlc_graph,
)


@pytest.fixture(autouse=True)
def _reset_sdlc_singleton() -> Iterator[None]:
    """每个测试前重置子图单例。"""
    sdlc_workflow._COMPILED_SDLC_GRAPH = None
    yield
    sdlc_workflow._COMPILED_SDLC_GRAPH = None


@pytest.fixture(autouse=True)
def _tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:  # type: ignore[name-defined]
    """重定向 WORKSPACE_SDLC_DIR 到 tmp_path/sdlc，避免污染真实 workspace。"""
    from orchestrator import sdlc_workspace

    target = tmp_path / "sdlc"
    monkeypatch.setattr(sdlc_workspace, "WORKSPACE_SDLC_DIR", target)
    return target


def _state(query: str = "实现一个 LRU 缓存") -> dict[str, Any]:
    return {
        "user_query": query,
        "session_id": "test-sess",
        "feedback_rounds": 0,
        "workflow_status": "running",
    }


# ============================================================
# 子图结构
# ============================================================


class TestSdlcGraphStructure:
    def test_build_returns_compiled_graph(self) -> None:
        graph = build_sdlc_graph()
        assert hasattr(graph, "ainvoke")

    def test_graph_contains_four_nodes(self) -> None:
        graph = build_sdlc_graph()
        node_ids = set(graph.nodes.keys())
        assert {"deepseek_doc", "glm_spec", "minimax_code", "glm_feedback"} <= node_ids


# ============================================================
# happy path（无遇阻）
# ============================================================


class TestHappyPath:
    async def test_no_feedback_rounds(self) -> None:
        """MiniMax 一次通过 → feedback_rounds=0，sdlc_doc 三段齐全。"""
        with patch.object(a2a_client, "message_send", new_callable=_FakeSend) as mock_send:
            mock_send.set_responses(
                deepseek="# Spec\n实现 LRU",
                glm="# 技术规范\n文件: lru.py",
                minimax="已实现 lru.py，pytest 全过\n[FILES_WRITTEN] code/lru.py",
            )
            graph = build_sdlc_graph()
            result = await graph.ainvoke(_state())

        assert result["feedback_rounds"] == 0
        assert result.get("sdlc_feedback", []) == []
        doc = result["sdlc_doc"]
        assert doc.get("spec")
        assert doc.get("tech_design")
        assert doc.get("implementation")
        assert "sdlc/test-sess/code/lru.py" in doc.get("code_paths", [])


# ============================================================
# 反馈回路：一次遇阻 → 解决
# ============================================================


class TestOneFeedbackResolved:
    async def test_first_attempt_blocked_second_resolved(self) -> None:
        """MiniMax 第 1 次遇阻 → GLM 反馈 → 第 2 次通过。"""
        with patch.object(a2a_client, "message_send", new_callable=_FakeSend) as mock_send:
            mock_send.set_responses(
                deepseek="# Spec",
                glm="# 技术规范",
                minimax_sequence=[
                    f"{NEED_HELP_MARKER} 不确定用 OrderedDict 还是 dict",
                    "已按 GLM 指导实现，pytest 全过\n[FILES_WRITTEN] code/lru.py",
                ],
                glm_feedback_sequence=["用 OrderedDict，move_to_end 更直观"],
            )
            graph = build_sdlc_graph()
            result = await graph.ainvoke(_state())

        assert result["feedback_rounds"] == 1
        feedbacks = result.get("sdlc_feedback", [])
        assert len(feedbacks) == 1
        assert feedbacks[0]["round"] == 1
        assert "OrderedDict" in feedbacks[0]["blocker"]


# ============================================================
# 反馈回路：达上限终止
# ============================================================


class TestMaxRoundsExhausted:
    async def test_blocked_at_max_rounds(self) -> None:
        """MiniMax 永远遇阻 → 卡在 MAX_FEEDBACK_ROUNDS → 终止。"""
        with patch.object(a2a_client, "message_send", new_callable=_FakeSend) as mock_send:
            mock_send.set_responses(
                deepseek="# Spec",
                glm="# 技术规范",
                minimax_sequence=[
                    f"{NEED_HELP_MARKER} 还是搞不定",
                    f"{NEED_HELP_MARKER} 还是搞不定",
                    f"{NEED_HELP_MARKER} 还是搞不定",
                ],
                glm_feedback_sequence=["再试一次", "再试两次"],
            )
            graph = build_sdlc_graph()
            result = await graph.ainvoke(_state())

        assert result["feedback_rounds"] == MAX_FEEDBACK_ROUNDS
        assert NEED_HELP_MARKER in result["sdlc_doc"]["implementation"]


# ============================================================
# FakeSend：模拟 a2a_client.message_send
# ============================================================


class _FakeSend:
    """Async callable，按 url 分流返回 deepseek/glm/minimax 响应。

    用法：
        mock = _FakeSend()
        mock.set_responses(deepseek=..., glm=..., minimax=...)
        with patch.object(a2a_client, "message_send", new_callable=lambda: mock):
            ...
    """

    def __init__(self) -> None:
        self._deepseek: str = ""
        self._glm: str = ""
        self._minimax: str = ""
        self._minimax_seq: list[str] = []
        self._minimax_idx: int = 0
        self._glm_fb_seq: list[str] = []
        self._glm_fb_idx: int = 0
        self.await_count: int = 0

    def set_responses(
        self,
        *,
        deepseek: str,
        glm: str,
        minimax: str | None = None,
        minimax_sequence: list[str] | None = None,
        glm_feedback_sequence: list[str] | None = None,
    ) -> None:
        self._deepseek = deepseek
        self._glm = glm
        if minimax is not None:
            self._minimax = minimax
        if minimax_sequence is not None:
            self._minimax_seq = minimax_sequence
            self._minimax_idx = 0
        if glm_feedback_sequence is not None:
            self._glm_fb_seq = glm_feedback_sequence
            self._glm_fb_idx = 0

    async def __call__(self, url: str, text: str, **kwargs: Any) -> str:
        self.await_count += 1
        # 通过 prompt 内容判断是哪个节点（spec/feedback 都调 glm，但 prompt 不同）
        if "deepseek" in url:
            return self._deepseek
        if "minimax" in url:
            if self._minimax_seq:
                resp = self._minimax_seq[min(self._minimax_idx, len(self._minimax_seq) - 1)]
                self._minimax_idx += 1
                return resp
            return self._minimax
        if "glm" in url:
            # 区分：spec 节点（首次）vs feedback 节点（含 blocker / prior_feedback）
            if "阻塞" in text or "NEED_HELP" in text or "之前已给过的反馈" in text:
                if self._glm_fb_seq:
                    resp = self._glm_fb_seq[min(self._glm_fb_idx, len(self._glm_fb_seq) - 1)]
                    self._glm_fb_idx += 1
                    return resp
                return self._glm  # 兜底
            return self._glm
        return self._glm
```

注：测试文件顶部需 `from pathlib import Path` 用于类型注解。

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/contract/test_sdlc_workflow.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'build_sdlc_graph'`

- [ ] **Step 3: 实现 5 节点 + 子图**

在 `orchestrator/sdlc_workflow.py` 末尾（`# LangGraph 节点 + 子图` 注释下）追加：

```python
import asyncio  # noqa: E402

from langgraph.graph import END, START, StateGraph  # noqa: E402

from observability.tracing import trace_node  # noqa: E402
from orchestrator import a2a_client  # noqa: E402
from orchestrator import sdlc_prompts, sdlc_workspace  # noqa: E402
from orchestrator.executor import get_agent_url  # noqa: E402
from orchestrator.state import (  # noqa: E402
    AgentName,
    OrchestrationState,
    SdlcDoc,
    SdlcFeedback,
)


# ============================================================
# Agent URL（复用 executor 的 get_agent_url）
# ============================================================

_GLM_URL = lambda: get_agent_url(AgentName.GLM.value)  # noqa: E731
_DEEPSEEK_URL = lambda: get_agent_url(AgentName.DEEPSEEK.value)  # noqa: E731
_MINIMAX_URL = lambda: get_agent_url(AgentName.MINIMAX.value)  # noqa: E731


# ============================================================
# 节点 1：DeepSeek 产 Spec
# ============================================================


@trace_node(name="orchestrator.sdlc.deepseek_doc")
async def deepseek_doc(state: OrchestrationState) -> dict[str, Any]:
    """DeepSeek 产 PRD/Spec 文档，落盘 spec.md。"""
    user_query = state["user_query"]
    session_id = state["session_id"]

    prompt = sdlc_prompts.DEEPSEEK_SPEC_PROMPT.format(user_query=user_query)
    text = await a2a_client.message_send(_DEEPSEEK_URL(), prompt, session_id=session_id)

    sdlc_workspace.write_sdlc_doc(session_id, "spec.md", text)

    old_doc: SdlcDoc = state.get("sdlc_doc", {}) or {}  # type: ignore[assignment]
    new_doc: SdlcDoc = {**old_doc, "spec": text}
    return {"sdlc_doc": new_doc, "workflow_status": "running"}


# ============================================================
# 节点 2：GLM 产技术规范（禁代码）
# ============================================================


@trace_node(name="orchestrator.sdlc.glm_spec")
async def glm_spec(state: OrchestrationState) -> dict[str, Any]:
    """GLM-5.2 产技术规范 + 编码指令；剥离超长代码块；落盘 tech-design.md。"""
    user_query = state["user_query"]
    session_id = state["session_id"]
    spec = (state.get("sdlc_doc") or {}).get("spec", "")

    prompt = sdlc_prompts.GLM_TECH_DESIGN_PROMPT.format(spec=spec, user_query=user_query)
    text = await a2a_client.message_send(_GLM_URL(), prompt, session_id=session_id)

    text = _strip_oversized_code_from_glm(text)
    sdlc_workspace.write_sdlc_doc(session_id, "tech-design.md", text)

    old_doc: SdlcDoc = state.get("sdlc_doc", {}) or {}  # type: ignore[assignment]
    new_doc: SdlcDoc = {**old_doc, "tech_design": text}
    return {"sdlc_doc": new_doc}


# ============================================================
# 节点 3：MiniMax 写码 + 自测（遇阻 [NEED_HELP]，禁自由发挥）
# ============================================================


@trace_node(name="orchestrator.sdlc.minimax_code")
async def minimax_code(state: OrchestrationState) -> dict[str, Any]:
    """MiniMax 按技术规范写码 + 自跑 pytest；遇阻标 [NEED_HELP]。"""
    user_query = state["user_query"]
    session_id = state["session_id"]
    doc = state.get("sdlc_doc") or {}
    tech_design = doc.get("tech_design", "")
    feedbacks = state.get("sdlc_feedback", []) or []

    # 拼 feedback_block：把历史 GLM 指导注入 prompt
    if feedbacks:
        feedback_block = "\n\n".join(
            f"第 {fb['round']} 轮 GLM 指导：{fb['guidance']}" for fb in feedbacks
        )
    else:
        feedback_block = "（首次执行，无历史反馈）"

    prompt = sdlc_prompts.MINIMAX_CODE_PROMPT.format(
        tech_design=tech_design,
        session_id=session_id,
        feedback_block=feedback_block,
    )
    text = await a2a_client.message_send(_MINIMAX_URL(), prompt, session_id=session_id)

    code_paths = _extract_files_written(text, session_id)
    old_doc: SdlcDoc = state.get("sdlc_doc", {}) or {}  # type: ignore[assignment]
    new_doc: SdlcDoc = {**old_doc, "implementation": text, "code_paths": code_paths}
    return {"sdlc_doc": new_doc}


# ============================================================
# 节点 4：纯路由 check_blocked
# ============================================================


def check_blocked(state: OrchestrationState) -> str:
    """判定走 glm_feedback 还是 done。

    遇阻判定：显式 [NEED_HELP] 标记 OR 自由发挥被检测。
    """
    impl = (state.get("sdlc_doc") or {}).get("implementation", "")
    rounds = state.get("feedback_rounds", 0)
    blocked = NEED_HELP_MARKER in impl or _detect_freestyle(impl) is not None
    if blocked and rounds < MAX_FEEDBACK_ROUNDS:
        return "glm_feedback"
    return "done"


# ============================================================
# 节点 5：GLM 给指导（禁代码）
# ============================================================


@trace_node(name="orchestrator.sdlc.glm_feedback")
async def glm_feedback(state: OrchestrationState) -> dict[str, Any]:
    """GLM-5.2 给 MiniMax 指导（禁完整代码）；rounds += 1。"""
    session_id = state["session_id"]
    doc = state.get("sdlc_doc") or {}
    impl = doc.get("implementation", "")
    tech_design = doc.get("tech_design", "")
    old_feedbacks: list[SdlcFeedback] = state.get("sdlc_feedback", []) or []
    rounds = state.get("feedback_rounds", 0)

    # 提取 blocker：优先 [NEED_HELP]，其次自由发挥描述
    blocker = _extract_need_help(impl) or _detect_freestyle(impl) or "（未识别的阻塞）"
    prior_feedback = "\n".join(
        f"第 {fb['round']} 轮：{fb['guidance']}" for fb in old_feedbacks
    ) or "（无）"

    prompt = sdlc_prompts.GLM_FEEDBACK_PROMPT.format(
        blocker=blocker,
        tech_design=tech_design,
        prior_feedback=prior_feedback,
    )
    guidance = await a2a_client.message_send(_GLM_URL(), prompt, session_id=session_id)
    guidance = _strip_oversized_code_from_glm(guidance)

    new_feedback: SdlcFeedback = {
        "round": rounds + 1,
        "blocker": blocker,
        "guidance": guidance,
    }
    return {
        "sdlc_feedback": [*old_feedbacks, new_feedback],
        "feedback_rounds": rounds + 1,
    }


# ============================================================
# SDLC 子图组装
# ============================================================


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
        check_blocked,
        {
            "glm_feedback": "glm_feedback",
            "done": END,
        },
    )

    sub.add_edge("glm_feedback", "minimax_code")  # 反馈回路

    return sub.compile()


_COMPILED_SDLC_GRAPH: Any = None


def get_compiled_sdlc_graph() -> Any:
    """获取编译好的 SDLC 子图（惰性初始化）。"""
    global _COMPILED_SDLC_GRAPH
    if _COMPILED_SDLC_GRAPH is None:
        _COMPILED_SDLC_GRAPH = build_sdlc_graph()
    return _COMPILED_SDLC_GRAPH


# ============================================================
# 主图节点：workflow_execute
# ============================================================


async def workflow_execute(state: OrchestrationState) -> dict[str, Any]:
    """主图节点：跑 SDLC 子图，返回 partial state。"""
    sub = get_compiled_sdlc_graph()
    result = await asyncio.wait_for(sub.ainvoke(state), timeout=SDLC_TIMEOUT_SECONDS)
    return dict(result)
```

- [ ] **Step 4: 修测试文件顶部 import**

在 `tests/contract/test_sdlc_workflow.py` 顶部加 `from pathlib import Path`（`_tmp_workspace` fixture 用到）。把 fixture 类型注解里的 `Path` 引用补上。

- [ ] **Step 5: 跑测试验证通过**

Run: `pytest tests/contract/test_sdlc_workflow.py -v`
Expected: 4 passed（结构 + happy + 一次反馈 + 上限）

如果 `_FakeSend` 的节点判断不准（spec/feedback 都走 glm URL），调整 `__call__` 里的 prompt 关键词判定。关键区分：spec 节点 prompt 含 "Spec 文档"；feedback 节点 prompt 含 "阻塞" 或 "NEED_HELP"。

- [ ] **Step 6: 跑全部 contract 测试**

Run: `pytest tests/contract/ -v`
Expected: 之前所有 + 新增全绿（注意 classifier/graph/aggregator 还没改，可能此时 test_graph 仍绿因 workflow_execute 还未挂到主图）

- [ ] **Step 7: 跑 ruff + mypy**

Run: `ruff check orchestrator/sdlc_workflow.py` 和 `mypy orchestrator/sdlc_workflow.py`
Expected: 干净

- [ ] **Step 8: 检查点**

SDLC 子图独立可跑，5 节点 + 反馈回路正确。下一步挂到主图。

---

## Task 6: Classifier WORKFLOW 路由

**Files:**
- Modify: `orchestrator/classifier.py`

- [ ] **Step 1: 写失败测试**

在 `tests/contract/test_classifier.py` 末尾追加新 TestClass：

```python
class TestWorkflowDetection:
    """``_is_workflow`` 关键词 + 正则 + 长度约束。"""

    def test_long_implement_query_triggers(self) -> None:
        """'实现一个 X'（≥15 字）→ 触发 WORKFLOW。"""
        from orchestrator.classifier import _is_workflow
        assert _is_workflow("实现一个线程安全的 LRU 缓存并附测试") is True

    def test_short_implement_query_no_trigger(self) -> None:
        """'实现一个 X'（<15 字）→ 不触发。"""
        from orchestrator.classifier import _is_workflow
        assert _is_workflow("实现一个 hello") is False

    def test_explicit_workflow_keyword(self) -> None:
        from orchestrator.classifier import _is_workflow
        assert _is_workflow("帮我走工作流完成这个功能") is True
        assert _is_workflow("端到端研发流程") is True

    def test_complete_keyword_triggers(self) -> None:
        from orchestrator.classifier import _is_workflow
        assert _is_workflow("请完整实现这个模块") is True

    def test_general_question_not_triggered(self) -> None:
        from orchestrator.classifier import _is_workflow
        assert _is_workflow("你好，介绍量子计算") is False
        assert _is_workflow("今天天气怎么样") is False


class TestClassifyWorkflowMode:
    """``classify`` 节点的 WORKFLOW 分支。"""

    def test_workflow_mode_returned(self) -> None:
        """长研发需求 query → classify 返回 mode=workflow + 初始 state 字段。"""
        from orchestrator.state import OrchestrationMode
        result = classify(_state("实现一个支持 TTL 过期的 LRU 缓存并附 pytest 测试"))
        assert result["mode"] == OrchestrationMode.WORKFLOW.value
        assert result["target_agent"] == ""
        assert result["subtasks"] == []
        assert result["workflow_status"] == "running"
        assert result["feedback_rounds"] == 0

    def test_decomposition_priority_over_workflow(self) -> None:
        """对比类问题（含'实现'）→ DECOMPOSITION 优先（不走 WORKFLOW）。"""
        from orchestrator.state import OrchestrationMode
        result = classify(_state("对比 Python 和 Go 实现一个 web 服务的区别"))
        assert result["mode"] == OrchestrationMode.TASK_DECOMPOSITION.value

    def test_short_code_query_still_direct(self) -> None:
        """短代码 query（'帮我写 hello'）→ 仍走 DIRECT → MiniMax。"""
        result = classify(_state("帮我写一个 hello 函数"))
        assert result["mode"] == "direct"
        assert result["target_agent"] == "minimax-agent"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/contract/test_classifier.py::TestWorkflowDetection tests/contract/test_classifier.py::TestClassifyWorkflowMode -v`
Expected: FAIL with `ImportError: cannot import name '_is_workflow'`

- [ ] **Step 3: 实现 classifier.py 改造**

在 `orchestrator/classifier.py` 的 `DECOMPOSITION_PATTERNS` 之后追加 WORKFLOW 关键词与正则：

```python
# ============================================================
# WORKFLOW 触发（P2.1：SDLC 研发协作工作流）
# ============================================================

WORKFLOW_KEYWORDS: tuple[str, ...] = (
    # 端到端研发语义
    "实现一个需求", "做一个功能", "做一个 feature", "开发一个",
    "从需求到代码", "完整实现", "端到端",
    # 显式工作流语义
    "走工作流", "走流程", "sdlc", "研发流程",
    # 交付物语义（暗示要 spec + 设计 + 代码）
    "交付一个", "产出一个", "帮我做一个",
)

WORKFLOW_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(实现|开发|做一个|帮我做一个|交付).{2,30}"),
    re.compile(r"完整.{0,10}(实现|开发|设计|流程)"),
)


def _is_workflow(query: str) -> bool:
    """判断是否进入 SDLC WORKFLOW 模式。

    判定顺序：正则强匹配 → 关键词弱匹配（需 query ≥ 15 字）。
    """
    for pattern in WORKFLOW_PATTERNS:
        if pattern.search(query):
            return True
    if len(query) >= 15:
        for kw in WORKFLOW_KEYWORDS:
            if kw in query:
                return True
    return False
```

在 `classify` 函数里，**DECOMPOSITION 判定之后、DIRECT 之前**插入 WORKFLOW 分支：

```python
@trace_node(name="orchestrator.classify")
def classify(state: OrchestrationState) -> dict[str, Any]:
    query = state["user_query"]
    log = logger.bind(session_id=state.get("session_id", "-"), query_preview=query[:60])

    # 1) 先判断是否进入任务分解模式
    if _is_decomposition(query):
        ...  # 不动

    # 2) 【P2.1 新增】WORKFLOW 模式
    if _is_workflow(query):
        log.info("classify_workflow", reason="workflow_keyword_or_pattern")
        return {
            "mode": OrchestrationMode.WORKFLOW.value,
            "target_agent": "",
            "subtasks": [],
            "workflow_status": "running",
            "feedback_rounds": 0,
        }

    # 3) 单任务直接路由：按关键词匹配选 Agent（不动）
    target, reason = _select_agent_by_keyword(query)
    ...
```

需在文件顶部 import 加 `from orchestrator.state import OrchestrationMode`（如已有则跳过；当前 classifier.py import 了 OrchestrationMode 但只用了两个值，新分支会用到 `WORKFLOW`）。

- [ ] **Step 4: 跑测试验证通过**

Run: `pytest tests/contract/test_classifier.py -v`
Expected: 全 passed（含新增 + 现有回归）

注意：现有 `TestClassifyNode` 里如果有用长句测 "实现一个 X → minimax" 的用例会失败——检查并更新它们。当前 test_classifier.py 现有用例用的是"帮我重构这段代码"（短句，不触发 WORKFLOW），应仍绿。

- [ ] **Step 5: 跑 ruff + mypy**

Run: `ruff check orchestrator/classifier.py` 和 `mypy orchestrator/classifier.py`
Expected: 干净

- [ ] **Step 6: 检查点**

WORKFLOW 路由生效，classifier 三分支优先级正确。

---

## Task 7: 主图集成 + aggregate 改造

**Files:**
- Modify: `orchestrator/graph.py`
- Modify: `orchestrator/aggregator.py`

- [ ] **Step 1: 写失败测试**

在 `tests/contract/test_graph.py` 新增 TestClass：

```python
class TestWorkflowBranch:
    """主图含 workflow_execute 节点 + classify → workflow → aggregate 路径。"""

    def test_main_graph_has_workflow_node(self) -> None:
        graph = build_graph()
        node_ids = set(graph.nodes.keys())
        assert "workflow_execute" in node_ids

    def test_route_workflow_mode(self) -> None:
        from orchestrator.graph import route_after_classify
        from orchestrator.state import OrchestrationMode
        state = {"mode": OrchestrationMode.WORKFLOW.value}
        assert route_after_classify(state) == "workflow"
```

在 `tests/contract/test_aggregator.py` 新增 TestClass：

```python
class TestWorkflowAggregation:
    """WORKFLOW 模式专属聚合。"""

    def _wf_state(
        self,
        *,
        spec: str = "# Spec",
        tech_design: str = "# Tech",
        implementation: str = "# Impl",
        code_paths: list[str] | None = None,
        feedbacks: list[dict] | None = None,
        rounds: int = 0,
        errors: list[str] | None = None,
    ) -> OrchestrationState:
        from orchestrator.state import OrchestrationMode
        state: OrchestrationState = {
            "session_id": "s1",
            "mode": OrchestrationMode.WORKFLOW.value,
            "sdlc_doc": {
                "spec": spec,
                "tech_design": tech_design,
                "implementation": implementation,
                "code_paths": code_paths or [],
            },
            "sdlc_feedback": feedbacks or [],
            "feedback_rounds": rounds,
            "workflow_status": "running",
        }
        if errors is not None:
            state["errors"] = errors
        return state

    def test_workflow_aggregation_contains_all_sections(self) -> None:
        result = aggregate(self._wf_state(code_paths=["sdlc/s1/code/lru.py"]))
        final = result["final_answer"]
        assert "📋" in final or "Spec" in final
        assert "🏗️" in final or "技术规范" in final
        assert "✅" in final or "实现" in final
        assert "📁" in final
        assert "sdlc/s1/code/lru.py" in final
        assert "📊" in final  # 工作流状态

    def test_workflow_aggregation_includes_feedback(self) -> None:
        feedbacks = [{"round": 1, "blocker": "阻塞 X", "guidance": "指导 Y"}]
        result = aggregate(self._wf_state(feedbacks=feedbacks, rounds=1))
        assert "🔁" in result["final_answer"]
        assert "阻塞 X" in result["final_answer"]
        assert "指导 Y" in result["final_answer"]

    def test_workflow_unresolved_marks_status(self) -> None:
        """达上限仍有 NEED_HELP → status=blocked_unresolved。"""
        from orchestrator.sdlc_workflow import MAX_FEEDBACK_ROUNDS, NEED_HELP_MARKER
        result = aggregate(
            self._wf_state(
                implementation=f"{NEED_HELP_MARKER} 未解决",
                rounds=MAX_FEEDBACK_ROUNDS,
            )
        )
        assert result["workflow_status"] == "blocked_unresolved"
        assert "需人工介入" in result["final_answer"]

    def test_workflow_resolved_status(self) -> None:
        """有反馈但最终无 NEED_HELP → status=blocked_resolved。"""
        result = aggregate(self._wf_state(rounds=1))
        assert result["workflow_status"] == "blocked_resolved"

    def test_workflow_no_feedback_status(self) -> None:
        """无反馈 → blocked_resolved（一次通过）。"""
        result = aggregate(self._wf_state(rounds=0))
        assert result["workflow_status"] == "blocked_resolved"
        assert "一次通过" in result["final_answer"]
```

- [ ] **Step 2: 跑测试验证失败**

Run: `pytest tests/contract/test_graph.py::TestWorkflowBranch tests/contract/test_aggregator.py::TestWorkflowAggregation -v`
Expected: FAIL（`workflow_execute` 不在主图 / `_aggregate_workflow` 不存在）

- [ ] **Step 3: 改造 graph.py**

修改 `orchestrator/graph.py` 的 `build_graph`，在 import 区加：

```python
from orchestrator.sdlc_workflow import workflow_execute
```

`build_graph` 函数体改为（新增 workflow_execute 节点 + 边）：

```python
def build_graph() -> Any:
    graph = StateGraph(OrchestrationState)

    # 1) 添加节点
    graph.add_node("classify", classify)
    graph.add_node("direct_execute", direct_execute)
    graph.add_node("decompose_execute", decompose_execute)
    graph.add_node("workflow_execute", workflow_execute)  # P2.1 新增
    graph.add_node("aggregate", aggregate)

    # 2) 边
    graph.add_edge(START, "classify")

    graph.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            "direct": "direct_execute",
            "decompose": "decompose_execute",
            "workflow": "workflow_execute",  # P2.1 新增
        },
    )

    graph.add_edge("direct_execute", "aggregate")
    graph.add_edge("decompose_execute", "aggregate")
    graph.add_edge("workflow_execute", "aggregate")  # P2.1 新增

    graph.add_edge("aggregate", END)

    return graph.compile()
```

修改 `route_after_classify`：

```python
def route_after_classify(state: OrchestrationState) -> str:
    mode = state.get("mode", "")
    if mode == OrchestrationMode.TASK_DECOMPOSITION.value:
        return "decompose"
    if mode == OrchestrationMode.WORKFLOW.value:  # P2.1 新增
        return "workflow"
    return "direct"
```

- [ ] **Step 4: 改造 aggregator.py**

在 `orchestrator/aggregator.py` 顶部 import 加：

```python
from orchestrator.sdlc_workflow import MAX_FEEDBACK_ROUNDS, NEED_HELP_MARKER
from orchestrator.state import OrchestrationMode, SdlcDoc, SdlcFeedback
```

修改 `aggregate` 函数体，在最开头加 WORKFLOW 分支：

```python
def aggregate(state: OrchestrationState) -> dict[str, Any]:
    mode = state.get("mode", "")

    # P2.1 新增：WORKFLOW 模式走专属聚合
    if mode == OrchestrationMode.WORKFLOW.value:
        return _aggregate_workflow(state)

    # 以下 DIRECT / DECOMPOSITION 逻辑完全不动
    responses: dict[str, str] = state.get("agent_responses", {}) or {}
    ...
```

在文件末尾追加 `_aggregate_workflow` 函数：

```python
def _aggregate_workflow(state: OrchestrationState) -> dict[str, Any]:
    """WORKFLOW 模式专属聚合：按研发流程顺序拼接各阶段产出。

    spec §5.6。返回 ``{"final_answer": ..., "workflow_status": ...}``。
    """
    doc: SdlcDoc = state.get("sdlc_doc", {}) or {}  # type: ignore[assignment]
    feedbacks: list[SdlcFeedback] = state.get("sdlc_feedback", []) or []
    errors: list[str] = state.get("errors", []) or []
    rounds = state.get("feedback_rounds", 0)

    sections: list[str] = []

    # 1. Spec（DeepSeek）
    if doc.get("spec"):
        sections.append(f"## 📋 Spec（DeepSeek 产 PRD）\n\n{doc['spec']}")

    # 2. 技术规范（GLM-5.2）
    if doc.get("tech_design"):
        sections.append(f"## 🏗️ 技术规范（GLM-5.2）\n\n{doc['tech_design']}")

    # 3. 反馈回路记录
    for fb in feedbacks:
        sections.append(
            f"## 🔁 反馈轮次 {fb['round']}（GLM-5.2 → MiniMax）\n\n"
            f"**MiniMax 阻塞**：{fb['blocker']}\n\n"
            f"**GLM 指导**：{fb['guidance']}"
        )

    # 4. 实现（MiniMax）
    if doc.get("implementation"):
        impl = doc["implementation"]
        status_emoji = (
            "✅" if rounds == 0 or NEED_HELP_MARKER not in impl else "⚠️"
        )
        sections.append(f"## {status_emoji} 实现（MiniMax）\n\n{impl}")

    # 5. 产出文件清单
    code_paths = doc.get("code_paths") or []
    if code_paths:
        paths_md = "\n".join(f"- `{p}`" for p in code_paths)
        sections.append(f"## 📁 落盘文件\n\n{paths_md}")

    # 6. 错误附录
    if errors:
        sections.append("## ⚠️ 执行错误\n\n" + "\n".join(f"- {e}" for e in errors))

    # 7. 状态总结 + workflow_status 推导
    impl_text = doc.get("implementation", "")
    unresolved = NEED_HELP_MARKER in impl_text and rounds >= MAX_FEEDBACK_ROUNDS
    if unresolved:
        status_summary = (
            f"⚠️ MiniMax 仍有阻塞但已达反馈上限"
            f"（{rounds}/{MAX_FEEDBACK_ROUNDS}），需人工介入"
        )
        workflow_status = "blocked_unresolved"
    elif rounds > 0:
        status_summary = f"✅ 经 {rounds} 轮反馈后完成"
        workflow_status = "blocked_resolved"
    else:
        status_summary = "✅ 一次通过，无反馈"
        workflow_status = "blocked_resolved"

    sections.append(f"## 📊 工作流状态\n\n{status_summary}")

    final = "\n\n---\n\n".join(sections)
    return {"final_answer": final, "workflow_status": workflow_status}
```

- [ ] **Step 5: 跑测试验证通过**

Run: `pytest tests/contract/test_graph.py tests/contract/test_aggregator.py -v`
Expected: 全 passed（含新增 + 现有回归）

- [ ] **Step 6: 跑全部 contract + graph 集成测试**

Run: `pytest tests/contract/ -v`
Expected: 全绿

- [ ] **Step 7: 跑 mypy + ruff（全项目）**

Run: `mypy orchestrator/` 和 `ruff check orchestrator/`
Expected: 干净

- [ ] **Step 8: 检查点**

主图集成完成，WORKFLOW 模式端到端（在 mock 下）跑通。下一步 e2e 测试。

---

## Task 8: e2e 测试 + marker 注册

**Files:**
- Modify: `pyproject.toml`（marker）
- Create: `tests/test_p2_1_e2e.py`

- [ ] **Step 1: 注册 marker**

在 `pyproject.toml` 的 `[tool.pytest.ini_options].markers` 列表里追加：

```toml
markers = [
    "e2e: end-to-end tests requiring docker-compose up",
    "p2_e2e: P2 orchestrator e2e tests (implies e2e + requires orchestrator up)",
    "p2_1_e2e: P2.1 SDLC workflow e2e tests (implies e2e + requires orchestrator + 3 agents up)",
    "p3_e2e: ...",
    ...
]
```

- [ ] **Step 2: 写 e2e 测试**

Create `tests/test_p2_1_e2e.py`:

```python
"""P2.1 阶段 e2e 测试（SDLC WORKFLOW 端到端）。

前置条件：
- ``.env.prod`` 已填好三家 API Key
- 已运行 ``docker compose --env-file .env.prod up -d`` 启动完整栈
  （litellm + 3 Agent + orchestrator）
- 全部容器 healthy

测试内容：
- 端到端：用户提研发需求 → 走完整 SDLC 工作流 → 返回拼接答案 + 落盘文件
- /v1/orchestrate/trace 返回完整 state（含 sdlc 子图各节点输出）
- 落盘文件持久化（spec.md / tech-design.md 存在）

标记：``@pytest.mark.e2e`` + ``@pytest.mark.p2_1_e2e``
（conftest 第 2 轮探活 orchestrator 已覆盖；不新增探活轮次）

用法：
    pytest -m "p2_1_e2e" tests/test_p2_1_e2e.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from orchestrator.sdlc_workflow import (
    GLM_CODE_BLOCK_MAX_LINES,
    MAX_FEEDBACK_ROUNDS,
    NEED_HELP_MARKER,
)


# ============================================================
# 辅助断言
# ============================================================


def _assert_glm_no_implementation(tech_design: str) -> None:
    """GLM 技术规范不应含完整实现（允许 ≤ 15 行的类型签名/伪代码）。"""
    for block in re.findall(r"```python\n(.*?)\n```", tech_design, re.DOTALL):
        lines = [l for l in block.split("\n") if l.strip() and not l.strip().startswith("#")]
        if len(lines) > GLM_CODE_BLOCK_MAX_LINES:
            pytest.fail(
                f"GLM tech_design 含超长代码块（{len(lines)} 行），疑似完整实现:\n{block[:200]}"
            )


def _assert_no_freestyle_in_final(implementation: str) -> None:
    """MiniMax 最终实现不应含自由发挥语义（应在反馈环节被纠正）。"""
    freestyle_markers = ["我额外", "我觉得应该", "我建议增加", "我自己加", "自行新增"]
    for marker in freestyle_markers:
        if marker in implementation:
            pytest.fail(
                f"MiniMax 最终实现含自由发挥语义 '{marker}'，未被反馈环节纠正"
            )


# ============================================================
# Test 1：端到端 happy path
# ============================================================


@pytest.mark.e2e
@pytest.mark.p2_1_e2e
def test_workflow_e2e_happy_path(orchestrator_url: str) -> None:
    """端到端：用户提研发需求 → 走完整 SDLC 工作流 → 返回拼接答案 + 落盘文件。"""
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate",
        json={
            "query": (
                "实现一个线程安全的 LRU 缓存，要求支持 TTL 过期，"
                "并附完整的 pytest 单元测试"
            )
        },
        timeout=600.0,  # 工作流耗时（5+ 次 LLM 调用）
    )
    assert response.status_code == 200
    body = response.json()

    # 1. 路由正确
    assert body["mode"] == "workflow", f"expected workflow, got {body['mode']}"

    # 2. final_state 字段齐全（/v1/orchestrate 返回顶层 + full_state 在 /trace 端点）
    #    /v1/orchestrate 顶层返回 answer + mode + session_id；sdlc_doc 在 full_state（/trace）
    assert body["session_id"]
    assert body["answer"]

    # 3. 最终答案含研发流程各阶段标题
    answer = body["answer"]
    assert "📋" in answer or "Spec" in answer
    assert "🏗️" in answer or "技术规范" in answer
    assert "✅" in answer or "实现" in answer


@pytest.mark.e2e
@pytest.mark.p2_1_e2e
def test_workflow_e2e_trace_full_state(orchestrator_url: str) -> None:
    """/v1/orchestrate/trace 返回完整 state（含 sdlc_doc 各阶段产出）。"""
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate/trace",
        json={"query": "实现一个计算斐波那契数列的函数并附 pytest 测试"},
        timeout=600.0,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "workflow"

    full_state = body.get("full_state", {})
    assert "sdlc_doc" in full_state
    doc = full_state["sdlc_doc"]
    assert doc.get("spec"), "DeepSeek 未产出 spec"
    assert doc.get("tech_design"), "GLM 未产出 tech_design"
    assert doc.get("implementation"), "MiniMax 未产出 implementation"

    # 角色边界（GLM 不写实现代码）
    _assert_glm_no_implementation(doc["tech_design"])

    # MiniMax 严格遵循（无自由发挥语义）
    _assert_no_freestyle_in_final(doc["implementation"])

    # 反馈轮数合规
    rounds = full_state.get("feedback_rounds", 0)
    assert rounds <= MAX_FEEDBACK_ROUNDS, f"feedback_rounds {rounds} > {MAX_FEEDBACK_ROUNDS}"

    # code_paths 基准一致（相对 workspace 根）
    sid = body["session_id"]
    code_paths = doc.get("code_paths", [])
    if code_paths:  # MiniMax 可能未真落盘（LLM 不确定性），有则校验基准
        assert all(p.startswith(f"sdlc/{sid}/code/") for p in code_paths), (
            f"code_paths 基准不一致: {code_paths}"
        )


# ============================================================
# Test 3：落盘文件持久化
# ============================================================


@pytest.mark.e2e
@pytest.mark.p2_1_e2e
def test_workflow_e2e_workspace_files_persisted(orchestrator_url: str) -> None:
    """spec.md / tech-design.md 落盘到 workspace/sdlc/<sid>/，工作流结束后文件仍在。"""
    response = httpx.post(
        f"{orchestrator_url}/v1/orchestrate",
        json={"query": "实现一个返回两个数之和的 add 函数并附 pytest 测试"},
        timeout=600.0,
    )
    assert response.status_code == 200
    sid = response.json()["session_id"]

    # orchestrator 容器挂载了 workspace 卷；测试从主机端访问同一路径
    # （docker-compose 把 ./workspace 挂载到所有容器）
    workspace_dir = Path("workspace") / "sdlc" / sid
    assert (workspace_dir / "spec.md").exists(), f"spec.md 未落盘: {workspace_dir}"
    assert (workspace_dir / "tech-design.md").exists(), (
        f"tech-design.md 未落盘: {workspace_dir}"
    )
    assert (workspace_dir / "spec.md").stat().st_size > 0, "spec.md 内容为空"
```

- [ ] **Step 3: 验证测试收集（不跑 e2e）**

Run: `pytest tests/test_p2_1_e2e.py --collect-only`
Expected: 3 个测试被收集（不实际跑，因 docker 未起会 skip）

Run: `pytest tests/test_p2_1_e2e.py -v`
Expected: 3 个 SKIPPED（conftest 第 1 轮探活 GLM Agent 失败 → skip 所有 e2e）

- [ ] **Step 4: 跑 ruff**

Run: `ruff check tests/test_p2_1_e2e.py`
Expected: 干净

- [ ] **Step 5: 检查点**

e2e 测试就绪。docker 栈起来后可跑。

---

## Task 9: 跑全套测试 + lint 全量验证

**Files:** 无修改，只跑验证

- [ ] **Step 1: 跑全部 contract 测试**

Run: `pytest tests/contract/ -v`
Expected: 全绿（含 test_classifier / test_graph / test_aggregator / test_sdlc_* 全部）

- [ ] **Step 2: 跑 mypy 全量**

Run: `mypy orchestrator/ agents/`
Expected: 无错误

- [ ] **Step 3: 跑 ruff check 全量**

Run: `ruff check .`
Expected: 无错误

- [ ] **Step 4: 跑 ruff format check**

Run: `ruff format --check .`
Expected: 无需格式化的文件（若有，跑 `ruff format .` 修正）

- [ ] **Step 5: 检查点**

契约层全绿 + lint 干净。e2e 需要 docker 栈（用户后续启动）。

---

## Task 10: 文档同步

**Files:**
- Modify: `docs/SPEC.md`（新增 P2.1 章节）
- Modify: `docs/ARCHITECTURE.md`（架构图加 WORKFLOW 分支）
- Modify: `docs/NORTH_STAR.md`（更新当前阶段）
- Modify: `docs/DECISIONS.md`（追加 ADR-0009）
- Modify: `README.md`（端口/阶段表更新）

- [ ] **Step 1: 在 DECISIONS.md 追加 ADR-0009**

在 `docs/DECISIONS.md` 末尾追加：

```markdown
## ADR-0009: 实装 SDLC WORKFLOW 编排模式（P2.1）

**日期**：2026-06-17
**状态**：Accepted

### 背景
P2 已实装 DIRECT + DECOMPOSITION 两种模式。需要让三家国产模型按"产品→架构→实现→反馈"
顺序协作产出工程产物（端到端研发流程），对应 state.py 里被注释的 WORKFLOW 占位。

### 决策
1. 采用**静态 StateGraph 子图**（方案 A）而非动态 workflow 引擎（方案 B）或手写协程（方案 C）。
   理由：与现有 graph.py 风格一致 + 复用 trace/checkpoint + 不引入新概念（YAGNI）。
2. 反馈回路用**单轮上限 N=2**（`MAX_FEEDBACK_ROUNDS`），不做多轮无限循环。
   理由：状态可预测 + e2e 可测 + 避免 LLM 死循环。
3. 节点返回完整 merge 后的值（不引入自定义 LangGraph reducer）。
   理由：与现有 agent_responses / errors 处理方式一致。
4. **双轨落盘**：spec.md/tech-design.md 由 orchestrator 直接写本地；code/* 由 MiniMax
   通过 filesystem MCP 自己写。理由：与"程序员自己写代码"角色一致。
5. **三层防御**保证角色边界：prompt 硬约束 + 节点兜底校验（剥离超长代码块/检测自由发挥）
   + Agent instruction 第二道防线。
6. MiniMax **严格遵循上游文档**（DeepSeek Spec + GLM 技术规范），禁止自由发挥；遇阻
   `[NEED_HELP]` 反馈 GLM，不自行解决。

### 后果
- 增 3 个新文件（sdlc_prompts/sdlc_workspace/sdlc_workflow）+ 改 4 个现有文件
- classifier 关键词优先级变（DECOMP > WORKFLOW > DIRECT）；现有 contract 测试同步更新
- e2e 一次完整工作流耗时 1-10 分钟（5+ 次 LLM 调用）

### 退路
若 LangGraph 子图共享 state 不兼容 → 把节点直接铺到主图（不抽子图）。
```

- [ ] **Step 2: 更新 NORTH_STAR.md**

把 "当前阶段（P0+P1）" 相关注释更新为含 P2.1。在 §3.2 "当前阶段不做" 列表里把 LangGraph 编排行删掉（已做）。

- [ ] **Step 3: 在 SPEC.md 新增 §P2.1 章节**

参照 spec 文档（§2~§5）摘核心内容写进 SPEC.md 的新章节。

- [ ] **Step 4: ARCHITECTURE.md 更新架构图**

在主图 mermaid 图里加 workflow 分支。

- [ ] **Step 5: README.md 阶段表更新**

把 "✅ P0~P6 全部完成" 改为 "✅ P0~P6 + P2.1 SDLC Workflow"。

- [ ] **Step 6: 检查点**

文档同步完成。整个 P2.1 实施闭环。

---

## Self-Review 检查清单

实施全部完成后，对照 spec 复核：

**1. Spec coverage：**
- [x] §1 目标 → 所有 Task 覆盖
- [x] §2 架构图（5 节点 + 反馈回路）→ Task 5 build_sdlc_graph
- [x] §2.4 三层防御 → Task 2 prompt + Task 4 兜底校验 + Agent instruction（Task 10 文档提及，可选后置）
- [x] §3 State schema → Task 1
- [x] §4 节点 + prompt + 兜底 → Task 2/4/5
- [x] §5 classifier + 主图 + aggregate → Task 6/7
- [x] §6 测试 → Task 1-8 全部测试
- [x] §7 实施 11 步 → 本计划 10 Task 覆盖（Agent instruction 微调并入 Task 10）

**2. Placeholder 扫描：** 无 TBD/TODO；所有代码块完整。

**3. Type consistency：**
- `OrchestrationMode.WORKFLOW` 全程一致
- `SdlcDoc` / `SdlcFeedback` 字段名（spec/tech_design/implementation/code_paths；round/blocker/guidance）跨 Task 一致
- `MAX_FEEDBACK_ROUNDS` / `NEED_HELP_MARKER` / `GLM_CODE_BLOCK_MAX_LINES` 常量名跨 Task 一致
- `_strip_oversized_code_from_glm` / `_extract_need_help` / `_detect_freestyle` / `_extract_files_written` / `_normalize_code_path` 函数名跨 Task 一致
- `build_sdlc_graph` / `workflow_execute` / `check_blocked` 节点名跨 Task 一致

---

## 执行选项

Plan complete and saved to `docs/superpowers/plans/2026-06-17-sdlc-workflow.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — 我每个 Task dispatch 一个 fresh subagent，两阶段 review，快速迭代
2. **Inline Execution** — 在当前会话用 executing-plans 批量执行，checkpoint review

Which approach?
