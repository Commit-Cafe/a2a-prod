"""calc.py 的 pytest 测试（供 MiniMax Agent 通过 shell MCP 调用 pytest）。"""

from __future__ import annotations

import pytest

from samples.calc import add, divide, fibonacci, multiply, subtract


def test_add() -> None:
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_subtract() -> None:
    assert subtract(5, 3) == 2


def test_multiply() -> None:
    assert multiply(3, 4) == 12


def test_divide() -> None:
    assert divide(10, 2) == 5.0


def test_divide_by_zero() -> None:
    with pytest.raises(ValueError, match="division by zero"):
        divide(1, 0)


def test_fibonacci() -> None:
    assert fibonacci(0) == []
    assert fibonacci(1) == [0]
    assert fibonacci(2) == [0, 1]
    assert fibonacci(10) == [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]


def test_fibonacci_negative() -> None:
    with pytest.raises(ValueError):
        fibonacci(-1)
