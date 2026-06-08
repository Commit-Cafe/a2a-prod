"""启动前校验 .env.prod 必填字段。

用法：
    python scripts/check_env.py
    python scripts/check_env.py --env-file .env.prod

退出码：
    0  全部通过
    1  缺失必填字段
    2  找不到 .env 文件
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NamedTuple

# 项目根目录（scripts/ 的上一级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 必填字段清单：字段名 + 简单非空校验提示
REQUIRED_KEYS: tuple[str, ...] = (
    "GLM_API_KEY",
    "DEEPSEEK_API_KEY",
    "MINIMAX_API_KEY",
    "LITELLM_MASTER_KEY",
)

# 数值/端口字段：仅做能转 int 的弱校验
INT_KEYS: tuple[str, ...] = (
    "LITELLM_PROXY_PORT",
    "GLM_AGENT_PORT",
    "DEEPSEEK_AGENT_PORT",
    "MINIMAX_AGENT_PORT",
)


class CheckResult(NamedTuple):
    ok: bool
    message: str


def _load_env(path: Path) -> dict[str, str]:
    """简易 .env 解析器，避免引入 python-dotenv 依赖到校验脚本。"""
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip("\"'")
    return result


def _check_required(env: dict[str, str]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for key in REQUIRED_KEYS:
        value = env.get(key, "")
        if not value or value.startswith("your_") or value.endswith("_here"):
            results.append(
                CheckResult(
                    ok=False,
                    message=f"[FAIL] {key} 未填写（仍在 .env.example 占位状态）",
                )
            )
        else:
            results.append(CheckResult(ok=True, message=f"[ OK ] {key} 已填写"))
    return results


def _check_int(env: dict[str, str]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for key in INT_KEYS:
        value = env.get(key, "")
        if not value:
            results.append(CheckResult(ok=False, message=f"[FAIL] {key} 缺失"))
            continue
        try:
            int(value)
        except ValueError:
            results.append(
                CheckResult(ok=False, message=f"[FAIL] {key}={value!r} 不是整数")
            )
        else:
            results.append(CheckResult(ok=True, message=f"[ OK ] {key}={value}"))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=str(PROJECT_ROOT / ".env.prod"),
        help="环境变量文件路径（默认：.env.prod）",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.is_file():
        sys.stderr.write(f"[FAIL] 找不到环境变量文件：{env_path}\n")
        sys.stderr.write(
            "提示：cp .env.example .env.prod，然后填入真实 API Key\n"
        )
        return 2

    env = _load_env(env_path)
    results = _check_required(env) + _check_int(env)

    print(f"检查 {env_path}：")
    for r in results:
        print(f"  {r.message}")

    failed = [r for r in results if not r.ok]
    if failed:
        print(f"\n{len(failed)} 项校验失败，请修复 .env.prod 后再启动服务。")
        return 1

    print("\n所有必填字段校验通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
