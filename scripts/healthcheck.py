"""Docker 容器内健康检查脚本。

被各 Agent / LiteLLM 容器的 Dockerfile HEALTHCHECK 引用。
通过环境变量 HEALTHCHECK_URL 决定探测哪个端点（默认 /health）。

用法（Dockerfile）：
    HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \\
        CMD python /app/scripts/healthcheck.py

退出码：
    0  健康
    1  不健康（HTTP 非 2xx 或无法连接）
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    url = os.environ.get("HEALTHCHECK_URL", "http://127.0.0.1:8000/health")
    timeout = float(os.environ.get("HEALTHCHECK_TIMEOUT", "3"))

    try:
        # 用 stdlib urllib，避免容器里额外装 requests
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        # HTTP 错误码（4xx/5xx）
        sys.stderr.write(f"[FAIL] {url} 返回 HTTP {e.code}\n")
        return 1
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        sys.stderr.write(f"[FAIL] 无法连接 {url}: {e}\n")
        return 1

    if 200 <= status < 300:
        print(f"[ OK ] {url} HTTP {status}")
        return 0

    sys.stderr.write(f"[FAIL] {url} 返回 HTTP {status}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
