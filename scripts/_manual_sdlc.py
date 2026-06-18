"""手动发一个 SDLC 工作流请求（带 auth header），验证编排链路。"""
import os
import sys
import time
from pathlib import Path

import httpx

# 加载 .env.prod 拿 LITELLM_MASTER_KEY
for line in Path(".env.prod").read_text(encoding="utf-8").split("\n"):
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

API_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
URL = "http://localhost:12080/v1/orchestrate"
QUERY = "实现一个返回两个数之和的 add 函数并附 pytest 测试"
SESSION_ID = f"manual-sdlc-{int(time.time())}"

body = {"query": QUERY, "session_id": SESSION_ID}
headers = {"Authorization": f"Bearer {API_KEY}"}
print(f"POST {URL}")
print(f"query: {QUERY}")
print(f"session: {SESSION_ID}")
print(f"auth: Bearer {API_KEY[:10]}...")
print("waiting (up to 9 min for full SDLC chain)...\n")

try:
    with httpx.Client(timeout=540.0) as client:
        resp = client.post(URL, json=body, headers=headers)
    print(f"status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"body: {resp.text[:2000]}")
        sys.exit(1)
    data = resp.json()
    print(f"\n=== MODE ===\n{data.get('mode')}")
    print(f"\n=== SESSION_ID ===\n{data.get('session_id')}")
    answer = data.get("answer", "")
    print(f"\n=== ANSWER (len={len(answer)}) ===")
    print(answer[:4000])
    if len(answer) > 4000:
        print(f"\n... [truncated, {len(answer) - 4000} more chars]")
    print(f"\n=== ERRORS ===\n{data.get('errors', [])}")
except httpx.ReadTimeout:
    print("TIMEOUT: orchestrator did not respond in 540s")
    sys.exit(2)
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
    sys.exit(3)
