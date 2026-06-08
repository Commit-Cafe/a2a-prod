# Copyright 2026 a2a-prod authors (SPDX-License-Identifier: MIT)
"""Filesystem MCP server (P3-3).

暴露 read_file / list_directory / write_file / create_directory 四个 tool，
强制沙箱化（WORKSPACE_ROOT），路径逃逸防护（realpath + 前缀校验）。

通过 Streamable HTTP 传输（默认 :12101/mcp）+ 独立 /healthz 端点。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

_MAX_READ_BYTES = 1024 * 1024  # 1MB 截断
_MAX_WRITE_BYTES = 5 * 1024 * 1024  # 5MB 写入上限

_WORKSPACE_ROOT_ENV = "WORKSPACE_ROOT"
_DEFAULT_WORKSPACE = "/app/workspace"

mcp: FastMCP = FastMCP(
    name="filesystem-mcp",
    stateless_http=True,
    json_response=True,
)


def _workspace_root() -> Path:
    raw = os.environ.get(_WORKSPACE_ROOT_ENV, _DEFAULT_WORKSPACE)
    return Path(raw).resolve()


def _resolve_safe(path: str) -> Path:
    """把相对路径解析到 WORKSPACE_ROOT 内；逃逸抛 PermissionError。"""
    if not path:
        raise ValueError("path is required")
    raw = path.lstrip("/\\")
    if not raw:
        raise ValueError("path is empty after normalization")
    root = _workspace_root()
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"path_escape: {path}") from exc
    return candidate


@mcp.custom_route("/healthz", methods=["GET"])  # type: ignore[untyped-decorator]
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse(
        {"status": "ok", "service": "filesystem-mcp", "workspace_root": str(_workspace_root())}
    )


@mcp.tool()
async def read_file(
    path: Annotated[str, Field(description="相对于 workspace 根的路径，例如 samples/calc.py")],
) -> str:
    """读取 workspace 内的文本文件，>1MB 截断到前 1MB。"""
    target = _resolve_safe(path)
    if not target.exists():
        raise FileNotFoundError(f"not_found: {path}")
    if not target.is_file():
        raise IsADirectoryError(f"not_a_file: {path}")
    data = target.read_bytes()
    if len(data) > _MAX_READ_BYTES:
        data = data[:_MAX_READ_BYTES]
    return data.decode("utf-8", errors="replace")


@mcp.tool()
async def list_directory(
    path: Annotated[str, Field(description="相对于 workspace 根的目录路径，例如 samples 或 .")],
) -> list[dict[str, object]]:
    """列出 workspace 内某目录的条目（按名字排序）。"""
    target = _resolve_safe(path)
    if not target.exists():
        raise FileNotFoundError(f"not_found: {path}")
    if not target.is_dir():
        raise NotADirectoryError(f"not_a_directory: {path}")
    entries: list[dict[str, object]] = []
    for child in sorted(target.iterdir(), key=lambda p: p.name):
        entry: dict[str, object] = {
            "name": child.name,
            "type": "directory" if child.is_dir() else "file",
        }
        if child.is_file():
            entry["size"] = child.stat().st_size
        entries.append(entry)
    return entries


@mcp.tool()
async def write_file(
    path: Annotated[str, Field(description="相对于 workspace 根的目标文件路径")],
    content: Annotated[str, Field(description="要写入的文本内容（覆盖式）")],
) -> str:
    """覆盖写入文本文件（自动创建父目录）。单次写入 <= 5MB。"""
    if len(content.encode("utf-8")) > _MAX_WRITE_BYTES:
        raise ValueError(f"content_too_large: max {_MAX_WRITE_BYTES} bytes")
    target = _resolve_safe(path)
    if target.exists() and target.is_dir():
        raise IsADirectoryError(f"target_is_directory: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} chars to {path}"


@mcp.tool()
async def create_directory(
    path: Annotated[str, Field(description="相对于 workspace 根的目标目录路径")],
) -> str:
    """创建目录（mkdir -p 语义）。"""
    target = _resolve_safe(path)
    if target.exists() and target.is_file():
        raise FileExistsError(f"target_is_file: {path}")
    target.mkdir(parents=True, exist_ok=True)
    return f"created {path}"


def main() -> None:
    """启动 filesystem MCP server（Streamable HTTP）。"""
    port = int(os.environ.get("MCP_FILESYSTEM_PORT", "12101"))
    host = os.environ.get("MCP_FILESYSTEM_HOST", "0.0.0.0")
    mcp.run(transport="streamable-http", host=host, port=port)  # type: ignore[call-arg]


if __name__ == "__main__":
    main()
