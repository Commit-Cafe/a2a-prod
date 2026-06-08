"""测试包。

测试组织：
- tests/unit/        纯单元测试，不依赖外部服务
- tests/test_p*_e2e.py  各阶段端到端测试，需要 docker compose up

运行：
    pytest -m unit           # 仅单元测试
    pytest -m e2e            # 仅 e2e（需 docker）
    pytest                   # 全部
"""

__all__: list[str] = []
