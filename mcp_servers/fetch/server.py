# Copyright 2026 a2a-prod authors (SPDX-License-Identifier: MIT)
"""Fetch MCP server (P3-4).

暴露 `fetch` tool：抓取 URL 并返回 Markdown / 原始 HTML。
按 ADR-0007 退出条件 #2 自研（httpx + html2text），保持与官方 mcp-server-fetch
工具签名一致（url / max_length / start_index / raw）。
"""

from __future__ import annotations

import os
from typing import Annotated
from urllib.parse import urlparse

import html2text
import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse

_MAX_FETCH_LENGTH = 50_000  # SPEC §3.7.3: max_length <= 50000
_DEFAULT_MAX_LENGTH = 5_000
_REQUEST_TIMEOUT_S = 30.0
_USER_AGENT = "a2a-prod-mcp-fetch/1.0"

_mcp: FastMCP = FastMCP(
    name="fetch-mcp",
    stateless_http=True,
    json_response=True,
    host=os.environ.get("MCP_FETCH_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_FETCH_PORT", "12102")),
)


def _validate_url(url: str) -> None:
    """校验 URL：必须是 http/https；其他 scheme 拒绝。"""
    if not url or not url.strip():
        raise ValueError("url is required")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"invalid_scheme: only http/https allowed, got {parsed.scheme!r}")
    if not parsed.netloc:
        raise ValueError("invalid_url: missing host")


async def _fetch_html(url: str) -> tuple[str, str]:
    """抓取 URL，返回 (content, final_url)；遵循重定向。"""
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=_REQUEST_TIMEOUT_S,
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text, str(resp.url)


def _html_to_markdown(html: str) -> str:
    converter = html2text.HTML2Text()
    converter.body_width = 0
    converter.ignore_images = True
    converter.ignore_links = True
    return converter.handle(html)


@_mcp.custom_route("/healthz", methods=["GET"])  # type: ignore[untyped-decorator]
async def healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "fetch-mcp"})


@_mcp.tool()
async def fetch(
    url: Annotated[str, Field(description="要抓取的 URL（必须 http/https）")],
    max_length: Annotated[
        int,
        Field(description="返回内容的最大字符数", ge=1, le=_MAX_FETCH_LENGTH),
    ] = _DEFAULT_MAX_LENGTH,
    start_index: Annotated[
        int, Field(description="从内容的哪个字符索引开始返回", ge=0)
    ] = 0,
    raw: Annotated[
        bool, Field(description="为 True 时返回原始 HTML，否则返回 Markdown")
    ] = False,
) -> dict[str, object]:
    """抓取 URL 并返回内容（默认 Markdown）。"""
    _validate_url(url)
    html, final_url = await _fetch_html(url)
    content = html if raw else _html_to_markdown(html)
    total = len(content)
    end_index = start_index + max_length
    chunk = content[start_index:end_index]
    return {
        "url": final_url,
        "content": chunk,
        "start_index": start_index,
        "end_index": min(end_index, total),
        "total_length": total,
        "truncated": end_index < total,
        "raw": raw,
    }


def main() -> None:
    """启动 fetch MCP server（Streamable HTTP）。

    host/port 在 FastMCP 构造时传入（mcp SDK 1.28+ 不再支持 run() 的 host/port 参数）。
    """
    _mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
