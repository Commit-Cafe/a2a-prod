"""基础设施脚本（启动前检查、健康检查等）。

P0 阶段将提供：
- check_env.py：启动前校验 .env.prod 必填字段、API Key 格式
- check_ports.ps1：检查端口 4000/12001-12003 是否被占用
- healthcheck.py：容器内健康检查脚本
"""
