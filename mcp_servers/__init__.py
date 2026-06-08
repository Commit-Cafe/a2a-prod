"""MCP Servers 自托管目录（P3 阶段实现）。

P0+P1+P2 阶段此包为空。
P3 阶段将在此实现：
- filesystem MCP server（read_file / write_file / list_directory）
- shell MCP server（run_command with whitelist）
- web_fetch MCP server（SSRF 防护）
"""
