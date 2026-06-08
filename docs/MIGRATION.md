# MIGRATION — 迁移与升级指南

> 本文档记录 a2a-prod 各阶段（P0~P6+）之间的迁移 / 升级步骤。
> 任何升级前 MUST 先读本文件对应章节。

---

## §1. 部署形态升级路径

| 当前部署 | 目标部署 | 步骤 | 风险 |
|---|---|---|---|
| 单机 docker-compose | 集群 docker-compose | 加 `scale: 3` + 共享 Redis / PG | 中（共享状态）|
| docker-compose | k3d / k3s | `infra/k8s/README.md` §快速开始 | 低（无状态服务可平滑切）|
| k3d / k3s | EKS / AKS / GKE | 改 `storageClass` + Ingress Controller | 中（Cloud-specific 配置）|
| EKS | 多集群 / 跨 region | 加 ArgoCD / Cluster API | 高（需规划网络）|

---

## §2. P 阶段内代码升级（minor version）

### P0 → P1 / P1 → P2 / P2 → P3 ...

本项目 minor 升级（如 `0.6.0 → 0.7.0`）通常伴随新增功能模块，**不需要数据迁移**。

升级步骤：

```bash
# 1. 拉新代码
git pull origin main

# 2. 同步依赖
uv pip install -e ".[dev]"   # 或 pip install -e ".[dev]"

# 3. 跑测试（验证无回归）
pytest -m "not e2e"           # 单元 + 契约（无需 docker）
docker compose --env-file .env.prod up -d
pytest -m e2e                 # 端到端（需 docker compose up）

# 4. 跑 lint / mypy
ruff check .
ruff format --check .
mypy agents host orchestrator
```

**重要**：升级前 MUST 读 [CHANGELOG](../../README.md) 与 [SPEC §5 变更日志](../SPEC.md)。

---

## §3. P6 阶段：docker-compose → K8s

### §3.1 迁移检查清单

| 步骤 | 验证方式 | 回滚方式 |
|---|---|---|
| 1. 备份 Langfuse 数据（如果已有生产数据）| `docker compose exec langfuse-postgres pg_dump ...` | N/A |
| 2. 准备本地 K8s 集群（k3d / minikube）| `kubectl get nodes` | `k3d cluster delete` |
| 3. 准备 `secrets.yaml` | `cat secrets.yaml.example` | 删 `secrets.yaml` |
| 4. `kubectl apply -k infra/k8s/` | `kubectl -n a2a-prod get all` | `kubectl delete -k infra/k8s/` |
| 5. 等待 Langfuse 启动（5-10 min） | `kubectl -n a2a-prod wait --for=condition=ready pod -l app.kubernetes.io/component=langfuse` | N/A |
| 6. 验证 OpenAI 兼容端点 | `curl /v1/models` | N/A |
| 7. 验证 trace 通路 | 调一次 → 看 Langfuse Dashboard | N/A |
| 8. 切前端用户流量 | 改 DNS / 反代 | 改回 DNS |

### §3.2 关键差异

#### 3.2.1 服务名

- docker-compose：`http://orchestrator:12080`
- K8s：同样 `http://orchestrator:12080`（Service 名相同 + 端口一致）

#### 3.2.2 持久化

- docker-compose：bind mount / named volume
- K8s：PVC（动态或静态 storageClass）

| 数据 | docker-compose | K8s |
|---|---|---|
| Langfuse Postgres | bind mount `./langfuse-data/postgres` | PVC `langfuse-postgres-data` (10Gi) |
| Langfuse ClickHouse | bind mount `./langfuse-data/clickhouse` | PVC `langfuse-clickhouse-data` (50Gi) |
| Langfuse MinIO | bind mount `./langfuse-data/minio` | PVC `langfuse-minio-data` (20Gi) |
| Open WebUI | bind mount `./open-webui-data` | PVC `open-webui-data` (5Gi) |

#### 3.2.3 镜像

- docker-compose：build-arg 切模块
- K8s：每个 workload 一个独立镜像（`a2a-prod-agent-glm:0.1.0` 等）

#### 3.2.4 健康检查

- docker-compose：单一 `healthcheck`
- K8s：startup + liveness + readiness 三探针

#### 3.2.5 滚动升级

- docker-compose：`docker compose up -d --no-deps <svc>`（手工）
- K8s：自动 RollingUpdate（maxSurge=1, maxUnavailable=0）

### §3.3 镜像构建

K8s 部署需要预构建好的镜像（不能 in-cluster build）。建议：

```bash
# 1. 3 Agent 镜像（用 build-arg 切模块）
docker build -t a2a-prod-agent-glm:0.1.0     -f agents/Dockerfile --build-arg AGENT_MODULE=glm_agent     .
docker build -t a2a-prod-agent-deepseek:0.1.0 -f agents/Dockerfile --build-arg AGENT_MODULE=deepseek_agent .
docker build -t a2a-prod-agent-minimax:0.1.0  -f agents/Dockerfile --build-arg AGENT_MODULE=minimax_agent  .

# 2. Orchestrator
docker build -t a2a-prod-orchestrator:0.7.0 -f host/Dockerfile .

# 3. LiteLLM（含 Langfuse SDK）
docker build -t a2a-prod-litellm:1.40.0-langfuse -f infra/litellm/Dockerfile .

# 4. 3 MCP server
docker build -t a2a-prod-mcp-filesystem:0.1.0 -f mcp_servers/filesystem/Dockerfile mcp_servers/filesystem/
docker build -t a2a-prod-mcp-fetch:0.1.0      -f mcp_servers/fetch/Dockerfile      mcp_servers/fetch/
docker build -t a2a-prod-mcp-shell:0.1.0      -f mcp_servers/shell/Dockerfile      mcp_servers/shell/

# 5. 推镜像到 registry
#    - 本地 minikube：直接 `minikube image load <image>` 或用本地 registry
#    - k3d：`k3d image import <image> -c a2a-prod`
#    - 云厂商：推到 ECR / ACR / GCR
```

---

## §4. P6 之后的生产化 TODO

P6 完成后（v0.8.0），距离"生产可用"还有以下工作：

| 主题 | 工具 / 方案 | 优先级 | 工作量 |
|---|---|---|---|
| TLS 证书 | cert-manager + Let's Encrypt | P0 | 1d |
| Ingress Controller | nginx-ingress / traefik | P0 | 0.5d |
| Secret 加密 | Sealed Secrets / SOPS / External Secrets | P0 | 1d |
| 监控指标 | Prometheus + Grafana | P1 | 2d |
| 日志聚合 | Loki / EFK | P1 | 2d |
| 告警 | Alertmanager | P1 | 0.5d |
| GitOps 部署 | ArgoCD / Flux | P1 | 1d |
| 备份 | Velero | P2 | 1d |
| 策略校验 | Kyverno / OPA | P2 | 1d |
| 多集群 | Cluster API | P3 | 5d |
| Service Mesh | Istio / Linkerd | P3 | 3d |

### §4.1 Sealed Secrets 示例

P6 阶段用 K8s Secret 明文（已 .gitignore）。生产强烈建议改 Sealed Secrets：

```bash
# 安装 Sealed Secrets controller
helm repo add sealed-secrets https://bitnami-labs.github.io/sealed-secrets
helm install sealed-secrets sealed-secrets/sealed-secrets -n kube-system

# 加密 secrets.yaml
kubeseal -f secrets.yaml -w sealed-secrets.yaml
# 提交 sealed-secrets.yaml 到 git；可解密但解密需 controller 私钥
```

详见 [infra/k8s/secrets.yaml.example](../../infra/k8s/secrets.yaml.example) 的注释。

### §4.2 cert-manager 示例

```bash
# 安装 cert-manager
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.0/cert-manager.yaml

# 创建 ClusterIssuer（Let's Encrypt）
cat <<EOF | kubectl apply -f -
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: ops@example.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
      - http01:
          ingress:
            class: nginx
EOF

# 给 Ingress 加 annotation
# cert-manager.io/cluster-issuer: "letsencrypt-prod"
```

---

## §5. 数据库 schema 升级

P0~P5 阶段所有有状态组件（Postgres / ClickHouse / MinIO）都是单一数据库，
**不**做 schema 升级（首次部署即最新）。

P6 之后如果：

- Langfuse 官方发布 breaking schema 变更 → 走 Langfuse 官方 migration 流程
  （Langfuse 启动时会自动跑 migration，无需手动）
- ClickHouse 升级 major 版本 → 走 Altinity 提供的 migration 工具
- MinIO 升级 → 用 `mc admin migrate` 工具

---

## §6. 配置变更追踪

| 配置项 | 引入版本 | 升级策略 |
|---|---|---|
| `LITELLM_MASTER_KEY` | P0 | 改后 MUST 重启所有 LiteLLM / Orchestrator / Agent |
| `GLM_API_KEY` / `DEEPSEEK_API_KEY` / `MiniMax_API_KEY` | P0 | 改后 MUST 重启 LiteLLM |
| `LANGFUSE_INIT_*` | P4 | 改后 MUST 重新跑 Langfuse init 脚本 |
| `ORCHESTRATOR_API_KEY` | P5 | 改后 MUST 重启 Orchestrator + Open WebUI |
| `MCP_FILESYSTEM_URL` 等 | P3 | 改后 MUST 重启对应 Agent |

K8s 中改 Secret 后：

```bash
# 1. 改 secrets.yaml
vim infra/k8s/secrets.yaml

# 2. 重新 apply
kubectl apply -f infra/k8s/secrets.yaml

# 3. 触发 Pod 滚动升级（K8s 不会自动感知 Secret 变化）
kubectl -n a2a-prod rollout restart deployment/<workload>

# 4. 验证
kubectl -n a2a-prod rollout status deployment/<workload>
```

---

## §7. 应急回滚

### §7.1 K8s 部署回滚

```bash
# 查看历史
kubectl -n a2a-prod rollout history deployment/orchestrator

# 回滚到上一个版本
kubectl -n a2a-prod rollout undo deployment/orchestrator

# 回滚到指定版本
kubectl -n a2a-prod rollout undo deployment/orchestrator --to-revision=2
```

### §7.2 整栈回滚

```bash
# 1. 备份当前状态
kubectl -n a2a-prod get all -o yaml > backup-$(date +%Y%m%d).yaml

# 2. 切回 docker-compose
docker compose --env-file .env.prod up -d

# 3. 验证
curl http://localhost:12080/health
```

### §7.3 数据回滚（Langfuse Postgres）

```bash
# K8s
kubectl -n a2a-prod exec langfuse-postgres-0 -- \
  pg_dump -U postgres -d postgres > backup-$(date +%Y%m%d).sql
```

---

## §8. 监控与日志接入（占位）

P6 之后接入 Prometheus + Grafana + Loki 时，应在：

- 旧监控：`Langfuse Dashboard` 看 trace
- 新监控：`Grafana` 看 metric + log

数据流：

```
[3 Agent / Orchestrator / MCP] --> OTEL --> [Langfuse OTEL collector] --> ClickHouse
                                        \
                                         --> stdout/stderr --> Loki

[LiteLLM] --> Prometheus client --> Prometheus --> Grafana
```

---

**最后更新**：2026-06-08（v0.8.0 P6 完成；新增 §3 K8s 迁移）
