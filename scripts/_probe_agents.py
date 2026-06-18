"""探活 3 个 agent 的实际响应（单点 LLM 调用耗时）。"""
import os
import time
from pathlib import Path

import httpx

for line in Path(".env.prod").read_text(encoding="utf-8").split("\n"):
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

API_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
AGENTS = [
    ("glm", "http://localhost:12001"),
    ("deepseek", "http://localhost:12002"),
    ("minimax", "http://localhost:12003"),
]

for name, base in AGENTS:
    print(f"\n=== {name} ({base}) ===")
    # A2A protocol: JSON-RPC message/send
    payload = {
        "jsonrpc": "2.0",
        "id": f"probe-{int(time.time())}",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": "回复两个字：pong"}],
                "messageId": f"m-{int(time.time())}",
                "kind": "message",
            },
        },
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    t0 = time.time()
    try:
        with httpx.Client(timeout=180.0) as c:
            r = c.post(f"{base}/", json=payload, headers=headers)
        elapsed = time.time() - t0
        print(f"  status: {r.status_code}  elapsed: {elapsed:.1f}s")
        if r.status_code == 200:
            data = r.json()
            # 提取返回文本
            result = data.get("result", {})
            parts = result.get("parts", [])
            text = next((p.get("text", "") for p in parts if p.get("type") == "text"), "")
            print(f"  reply: {text[:80]!r}")
        else:
            print(f"  body: {r.text[:300]}")
    except httpx.ReadTimeout:
        print(f"  TIMEOUT after {time.time() - t0:.1f}s")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
