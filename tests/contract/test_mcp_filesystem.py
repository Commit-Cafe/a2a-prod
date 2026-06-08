"""Filesystem MCP server 契约测试（SPEC §3.7.2）。

守护点：
- 4 个 tool 必须注册：read_file / list_directory / write_file / create_directory
- 路径逃逸防护：``../`` / 绝对路径 / symlink 逃逸 MUST 抛 PermissionError
- write_file 大小上限：> 5MB MUST 抛 ValueError
- read_file 大小上限：> 1MB MUST 截断到 1MB
- list_directory：返回 dict 列表，按 name 排序

不依赖 docker；用 tmp_path + monkeypatch WORKSPACE_ROOT 模拟沙箱。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mcp_servers.filesystem.server import (
    _MAX_READ_BYTES,
    _MAX_WRITE_BYTES,
    _resolve_safe,
    create_directory,
    list_directory,
    mcp,
    read_file,
    write_file,
)

# ============================================================
# 公共 fixture：tmp_path 作为 WORKSPACE_ROOT
# ============================================================


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 WORKSPACE_ROOT 指到 tmp_path，并预置 samples 目录。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    samples = tmp_path / "samples"
    samples.mkdir()
    (samples / "hello.txt").write_text("hello world", encoding="utf-8")
    (samples / "sub").mkdir()
    (samples / "sub" / "nested.py").write_text("print('nested')\n", encoding="utf-8")
    return tmp_path


# ============================================================
# 工具注册（SPEC §3.7.2 MUST 暴露 4 个 tool）
# ============================================================


class TestToolRegistration:
    """FastMCP 必须注册 4 个 tool。"""

    def _tool_names(self) -> set[str]:
        tools = mcp._tool_manager._tools
        return set(tools.keys())

    def test_has_read_file(self) -> None:
        assert "read_file" in self._tool_names()

    def test_has_list_directory(self) -> None:
        assert "list_directory" in self._tool_names()

    def test_has_write_file(self) -> None:
        assert "write_file" in self._tool_names()

    def test_has_create_directory(self) -> None:
        assert "create_directory" in self._tool_names()


# ============================================================
# 路径逃逸防护（SPEC §3.7.2 安全 MUST）
# ============================================================


class TestPathEscapeProtection:
    """``_resolve_safe`` 必须拒绝任何逃逸 WORKSPACE_ROOT 的路径。"""

    def test_normal_relative_path(self, workspace: Path) -> None:
        resolved = _resolve_safe("samples/hello.txt")
        assert resolved == (workspace / "samples" / "hello.txt").resolve()

    def test_leading_slash_normalized(self, workspace: Path) -> None:
        """带前导 ``/`` 的路径也要被规范化到 workspace 内。"""
        resolved = _resolve_safe("/samples/hello.txt")
        assert resolved == (workspace / "samples" / "hello.txt").resolve()

    def test_dotdot_escape_rejected(self, workspace: Path) -> None:
        with pytest.raises(PermissionError, match="path_escape"):
            _resolve_safe("../../etc/passwd")

    def test_dotdot_with_subpath_escape(self, workspace: Path) -> None:
        with pytest.raises(PermissionError, match="path_escape"):
            _resolve_safe("samples/../../../etc/shadow")

    def test_empty_path_rejected(self, workspace: Path) -> None:
        with pytest.raises(ValueError, match="path is required"):
            _resolve_safe("")

    def test_only_slashes_rejected(self, workspace: Path) -> None:
        with pytest.raises(ValueError, match="path is empty"):
            _resolve_safe("///")

    def test_symlink_escape_rejected(self, workspace: Path) -> None:
        """符号链接指向 workspace 外的文件也算逃逸。

        Windows 上 symlink 创建需要开发者模式或管理员；创建失败或创建后未被识别为
        symlink 时 skip（避免假阳性）。
        """
        outside = workspace.parent / f"outside_{os.urandom(4).hex()}.txt"
        outside.write_text("secret", encoding="utf-8")
        link_path = workspace / "samples" / "evil_link.txt"
        try:
            link_path.symlink_to(outside)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"symlink not supported on this platform: {exc}")
        if not link_path.is_symlink():
            pytest.skip("symlink created but not recognized (likely Windows without dev mode)")
        try:
            with pytest.raises(PermissionError, match="path_escape"):
                _resolve_safe("samples/evil_link.txt")
        finally:
            outside.unlink(missing_ok=True)


# ============================================================
# read_file 行为
# ============================================================


class TestReadFile:
    async def test_read_existing_file(self, workspace: Path) -> None:
        content = await read_file("samples/hello.txt")
        assert content == "hello world"

    async def test_read_not_found(self, workspace: Path) -> None:
        with pytest.raises(FileNotFoundError, match="not_found"):
            await read_file("samples/no_such_file.txt")

    async def test_read_directory_rejected(self, workspace: Path) -> None:
        with pytest.raises(IsADirectoryError, match="not_a_file"):
            await read_file("samples/sub")

    async def test_read_escape_rejected(self, workspace: Path) -> None:
        with pytest.raises(PermissionError, match="path_escape"):
            await read_file("../../etc/passwd")

    async def test_read_truncates_large_file(self, workspace: Path) -> None:
        big = workspace / "big.bin"
        big.write_bytes(b"x" * (_MAX_READ_BYTES + 1024))
        content = await read_file("big.bin")
        assert len(content.encode("utf-8")) == _MAX_READ_BYTES


# ============================================================
# list_directory 行为
# ============================================================


class TestListDirectory:
    async def test_list_directory_sorted(self, workspace: Path) -> None:
        entries = await list_directory("samples")
        names = [e["name"] for e in entries]
        assert names == sorted(names)
        # 包含预置的 hello.txt、sub
        name_set = set(names)
        assert "hello.txt" in name_set
        assert "sub" in name_set

    async def test_list_directory_entry_shape(self, workspace: Path) -> None:
        entries = await list_directory("samples")
        hello = next(e for e in entries if e["name"] == "hello.txt")
        assert hello["type"] == "file"
        assert isinstance(hello.get("size"), int) and hello["size"] > 0
        sub = next(e for e in entries if e["name"] == "sub")
        assert sub["type"] == "directory"
        assert "size" not in sub  # 目录不带 size

    async def test_list_not_found(self, workspace: Path) -> None:
        with pytest.raises(FileNotFoundError, match="not_found"):
            await list_directory("no_such_dir")

    async def test_list_file_rejected(self, workspace: Path) -> None:
        with pytest.raises(NotADirectoryError, match="not_a_directory"):
            await list_directory("samples/hello.txt")


# ============================================================
# write_file 行为
# ============================================================


class TestWriteFile:
    async def test_write_creates_parent(self, workspace: Path) -> None:
        result = await write_file("new_dir/nested/file.txt", "abc")
        assert "wrote 3 chars" in result
        assert (workspace / "new_dir" / "nested" / "file.txt").read_text() == "abc"

    async def test_write_overwrites_existing(self, workspace: Path) -> None:
        await write_file("samples/hello.txt", "replaced")
        assert (workspace / "samples" / "hello.txt").read_text() == "replaced"

    async def test_write_to_directory_rejected(self, workspace: Path) -> None:
        with pytest.raises(IsADirectoryError, match="target_is_directory"):
            await write_file("samples/sub", "can't write to dir")

    async def test_write_too_large_rejected(self, workspace: Path) -> None:
        big = "x" * (_MAX_WRITE_BYTES + 1)
        with pytest.raises(ValueError, match="content_too_large"):
            await write_file("too_big.txt", big)

    async def test_write_escape_rejected(self, workspace: Path) -> None:
        with pytest.raises(PermissionError, match="path_escape"):
            await write_file("../../evil.txt", "nope")


# ============================================================
# create_directory 行为
# ============================================================


class TestCreateDirectory:
    async def test_create_nested(self, workspace: Path) -> None:
        result = await create_directory("a/b/c")
        assert "created" in result
        assert (workspace / "a" / "b" / "c").is_dir()

    async def test_create_idempotent(self, workspace: Path) -> None:
        await create_directory("x")
        result = await create_directory("x")  # 第二次不报错
        assert "created" in result

    async def test_create_over_file_rejected(self, workspace: Path) -> None:
        with pytest.raises(FileExistsError, match="target_is_file"):
            await create_directory("samples/hello.txt")
