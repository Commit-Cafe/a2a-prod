"""简易计算器模块（供 P3 e2e 测试：代码审查 / pytest 调用 / 读写练习）。

故意保留若干可被 LLM 识别的小问题（命名、异常处理、文档），
方便 GLM Agent 在代码审查场景下给出意见。
"""

from __future__ import annotations


def add(a: float, b: float) -> float:
    return a + b


def subtract(a: float, b: float) -> float:
    return a - b


def multiply(a: float, b: float) -> float:
    return a * b


def divide(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("division by zero")
    return a / b


def fibonacci(n: int) -> list[int]:
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return []
    if n == 1:
        return [0]
    seq = [0, 1]
    for _ in range(2, n):
        seq.append(seq[-1] + seq[-2])
    return seq
