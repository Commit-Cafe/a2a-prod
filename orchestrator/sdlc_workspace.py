"""SDLC 工作流 workspace 落盘辅助（P2.1）。

职责：
- 提供 ``WORKSPACE_SDLC_DIR`` 常量（``workspace/sdlc``，可被 env 覆盖）
- ``write_sdlc_doc``：orchestrator 直接写 spec.md / tech-design.md 到本地

落盘策略（spec §4.5 双轨落盘）：
- spec.md / tech-design.md → orchestrator 直接写本地（本模块）
- code/* → MiniMax 通过 filesystem MCP 自己写（不在本模块；由 ``sdlc_workflow._extract_files_written`` 解析）

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
