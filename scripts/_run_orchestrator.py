"""用新镜像直接 docker run orchestrator 容器（绕过 compose 的 litellm rebuild）。

从 .env.prod 提取 orchestrator 需要的 env，生成 docker run 命令并执行。
"""
import subprocess
import sys
from pathlib import Path

# 从 .env.prod 读所有非空、非注释的 env
env_path = Path(".env.prod")
env_pairs = []
for line in env_path.read_text(encoding="utf-8").split("\n"):
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        env_pairs.append(line)

# 找 compose 创建的网络名
result = subprocess.run(
    ["docker", "network", "ls", "--format", "{{.Name}}", "--filter", "name=a2a"],
    capture_output=True, text=True, check=True,
)
networks = [n for n in result.stdout.strip().split("\n") if n]
network = networks[0] if networks else "a2a-prod-net"
print(f"network: {network}")

# 构建 docker run 命令
cmd = [
    "docker", "run", "-d",
    "--name", "a2a-prod-orchestrator",
    "--network", network,
    "-p", "12080:12080",
    "-v", f"{Path.cwd()}\\workspace:/app/workspace",
]
for pair in env_pairs:
    cmd.extend(["-e", pair])
# 确保关键运行时配置
cmd.extend(["-e", "AGENT_MODULE=orchestrator", "-e", "PORT=12080", "-e", "HOST=0.0.0.0"])
cmd.append("a2a-prod-orchestrator:0.1.0")

print(f"running: docker run ... a2a-prod-orchestrator:0.1.0")
print(f"env count: {len(env_pairs)}")
result = subprocess.run(cmd, capture_output=True, text=True)
print(f"exit: {result.returncode}")
if result.stdout.strip():
    print(f"container id: {result.stdout.strip()[:12]}")
if result.stderr.strip():
    print(f"stderr: {result.stderr[:500]}")
sys.exit(result.returncode)
