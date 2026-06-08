# scripts/

> 运维脚本目录。

## 脚本清单（P0 阶段将提供）

| 脚本 | 用途 | 调用方式 |
|---|---|---|
| `check_env.py` | 启动前校验 .env.prod 必填字段 | `python scripts/check_env.py` |
| `check_ports.ps1` | 检查 4000/12001-12003 端口占用 | `.\scripts\check_ports.ps1` |
| `healthcheck.py` | Docker 容器内健康检查 | Dockerfile HEALTHCHECK 引用 |

## P1+ 阶段将补充

- `seed_history.py`：从原 a2a-agents history/ 导入会话历史到 Langfuse
- `bench_response.py`：基准测试三 Agent 响应时延
