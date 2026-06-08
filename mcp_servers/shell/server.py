# Copyright 2026 a2a-prod authors (SPDX-License-Identifier: MIT)
"""Shell MCP server (P3-5).

暴露 `run_command` tool：在 allowlist 内执行命令，强制 30s 超时。

安全约束（详见 SPEC §3.7.4）：
- 命令 allowlist：pytest / ruff / mypy / git / cat / ls
- 子命令二级白名单（如 git 只允许 status/diff/log/show）
- 禁止 shell 元字符：& ; | ` > < $(
- subprocess.run(shell=False, timeout=30)
- cwd 必须在 WORKSPACE_ROOT 内（realpath + 前缀校验）
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

_DEFAULT_TIMEOUT_S = 30.0
_MAX_OUTPUT_BYTES = 1_000_000  # 1MB

_COMMAND_ALLOWLIST: frozenset[str] = frozenset(
    {"pytest", "ruff", "mypy", "git", "cat", "ls"}
)

_SUBCOMMAND_WHITELIST: dict[str, frozenset[str]] = {
    "git": frozenset({"status", "diff", "log", "show"}),
}

_FORBIDDEN_CHARS: tuple[str, ...] = ("&", ";", "|", "`", ">", "<", "$(")

_WORKSPACE_ROOT_ENV = "WORKSPACE_ROOT"
_DEFAULT_WORKSPACE = "/app/workspace"

_mcp: FastMCP = FastMCP(
    name="shell-mcp",
    stateless_http=True,
    json_response=True,
)


def _workspace_root() -> Path:
    return Path(os.environ.get(_WORKSPACE_ROOT_ENV, _DEFAULT_WORKSPACE)).resolve()


def _validate_command(command: str) -> list[str]:
    """校验命令字符串，返回 argv list。失败抛 ValueError。"""
    if not command or not command.strip():
        raise ValueError("command is required")
    for char in _FORBIDDEN_CHARS:
        if char in command:
            raise ValueError(f"shell_metachar_forbidden: '{char}' not allowed")
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError(f"parse_error: {exc}") from exc
    if not argv:
        raise ValueError("command is empty after parsing")
    head = argv[0]
    if head not in _COMMAND_ALLOWLIST:
        raise ValueError(f"not_in_allowlist: '{head}' not allowed")
    if head in _SUBCOMMAND_WHITELIST:
        sub = argv[1] if len(argv) > 1 else ""
        allowed = _SUBCOMMAND_WHITELIST[head]
        if sub not in allowed:
            raise ValueError(
                f"subcommand_not_allowed: '{head} {sub}' not in {sorted(allowed)}"
            )
    return argv


def _resolve_cwd(cwd: str) -> Path:
    """校验 cwd 必须在 WORKSPACE_ROOT 内。"""
    raw = cwd.strip() if cwd else "."
    if not raw:
        raw = "."
    raw = raw.lstrip("/\\")
    root = _workspace_root()
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"cwd_escape: {cwd}") from exc
    if not candidate.exists():
        raise FileNotFoundError(f"cwd_not_found: {cwd}")
    if not candidate.is_dir():
        raise NotADirectoryError(f"cwd_not_directory: {cwd}")
    return candidate


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_BYTES:
        return text
    return text[:_MAX_OUTPUT_BYTES] + "\n[truncated]"


@_mcp.custom_route("/healthz", methods=["GET"])  # type: ignore[untyped-decorator]
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": "shell-mcp",
            "allowlist": sorted(_COMMAND_ALLOWLIST),
        }
    )


@_mcp.tool()
async def run_command(
    command: Annotated[str, Field(description="要执行的命令（必须在 allowlist 内）")],
    cwd: Annotated[
        str, Field(description="工作目录（相对 workspace 根，默认 '.'）")
    ] = ".",
) -> dict[str, object]:
    """在 allowlist 内执行 shell 命令，30s 超时。返回 stdout/stderr/exit_code。"""
    argv = _validate_command(command)
    working_dir = _resolve_cwd(cwd)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(working_dir),
            capture_output=True,
            timeout=_DEFAULT_TIMEOUT_S,
            check=False,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"timeout: exceeded {_DEFAULT_TIMEOUT_S}s") from exc

    root = _workspace_root()
    return {
        "command": command,
        "argv": argv,
        "cwd": str(working_dir.relative_to(root))
        if working_dir != root
        else ".",
        "exit_code": proc.returncode,
        "stdout": _truncate(proc.stdout or ""),
        "stderr": _truncate(proc.stderr or ""),
    }


def main() -> None:
    """启动 shell MCP server（Streamable HTTP）。"""
    port = int(os.environ.get("MCP_SHELL_PORT", "12103"))
    host = os.environ.get("MCP_SHELL_HOST", "0.0.0.0")
    _mcp.run(transport="streamable-http", host=host, port=port)  # type: ignore[call-arg]


if __name__ == "__main__":
    main()
