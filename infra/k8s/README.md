# infra/k8s/

> Kubernetes 部署清单（v0.8.0，P6 阶段）。详见 [SPEC §3.10](../../docs/SPEC.md) / [ADR-0010](../../docs/DECISIONS.md)。

## 目录结构

```
infra/k8s/
├── README.md                  # 本文件（部署指南）
├── kustomization.yaml         # Kustomize 入口
├── secrets.yaml.example       # Secret 模板（gitignore：复制为 secrets.yaml 后再 apply）
├── 00-namespace.yaml          # Namespace + ResourceQuota + LimitRange + default-deny NetworkPolicy
├── 01-rbac.yaml               # ServiceAccount（每个 workload 一个）
├── 02-configmap.yaml          # ConfigMap（litellm config / langfuse web env / shared）
├── 03-pvc.yaml                # PersistentVolumeClaim（4 个数据卷）
├── 10-litellm.yaml            # LiteLLM Proxy
├── 20-agents.yaml             # 3 Agent
├── 30-orchestrator.yaml       # Orchestrator + Ingress 模板
├── 40-mcp.yaml                # 3 MCP Server
├── 50-langfuse.yaml           # Langfuse 6 组件
├── 60-open-webui.yaml         # Open WebUI
├── 70-network-policies.yaml   # NetworkPolicy（显式 allow）
├── 80-pdb.yaml                # PodDisruptionBudget
└── tests/                     # 清单验证脚本
    └── test_manifests.py
```

## 快速开始（minikube / k3s 本地验证）

### 前置条件

- `kubectl` ≥ 1.28
- `kustomize` ≥ 5.0（或 `kubectl apply -k` 内置）
- minikube / k3d / kind 任一（推荐 k3d，资源占用低）
- 三家 LLM API Key + Langfuse 强凭证 + 一个 storageClass

```bash
# 1. 起本地集群（k3d 示例）
k3d cluster create a2a-prod --agents 2 --servers 1

# 2. 准备 Secret（⚠ 不要提交 secrets.yaml 到 git）
cp secrets.yaml.example secrets.yaml
vim secrets.yaml  # 替换所有 REPLACE_ME / CHANGE-ME

# 3. 应用全部
kubectl apply -k .

# 4. 等待 ready（5-10 分钟，Langfuse init 最慢）
kubectl -n a2a-prod wait --for=condition=ready pod -l app.kubernetes.io/part-of=a2a-prod --timeout=600s

# 5. 暴露服务（k3d 示例；生产用 Ingress Controller）
kubectl -n a2a-prod port-forward svc/orchestrator 12080:12080 &
kubectl -n a2a-prod port-forward svc/open-webui 8080:8080 &
kubectl -n a2a-prod port-forward svc/langfuse-web 3000:3000 &

# 6. 验证
curl http://localhost:12080/health
curl http://localhost:12080/v1/models -H "Authorization: Bearer $(grep LITELLM_MASTER_KEY secrets.yaml | cut -d= -f2 | tr -d '\"')"
```

## 设计要点

### 1. 资源分层

| 资源类型 | K8s 对象 | 文件 | 副本数 |
|---|---|---|---|
| LLM 路由 | Deployment + Service + HPA | `10-litellm.yaml` | 2 (auto 2-8) |
| 3 Agent | Deployment + Service + HPA | `20-agents.yaml` | 2 (auto 2-6) |
| 编排器 | Deployment + Service + HPA | `30-orchestrator.yaml` | 2 (auto 2-8) |
| 3 MCP | Deployment + Service | `40-mcp.yaml` | 1 |
| Langfuse 6 组件 | Deployment + Service | `50-langfuse.yaml` | 各 1-2 |
| Open WebUI | Deployment + Service | `60-open-webui.yaml` | 1 (Recreate) |

### 2. 健康检查三件套

每个有 HTTP 端点的工作负载都配 **startup + liveness + readiness** 三探针：

- **startupProbe**：5min 启动宽限（Langfuse init 最慢）
- **livenessProbe**：30s 一次，失败重启
- **readinessProbe**：10s 一次，决定是否接流量

详见 [SPEC §3.10.3](../../docs/SPEC.md)。

### 3. 安全基线

- **非 root**：`runAsNonRoot: true` + `runAsUser: 1000`
- **只读根文件系统**：`readOnlyRootFilesystem: true`（可写目录走 emptyDir / PVC）
- **禁提权**：`allowPrivilegeEscalation: false`
- **drop ALL capabilities**
- **Secret 必走 K8s Secret**：API Key / DB 密码 / NextAuth 密钥全部 `secretKeyRef` 注入

### 4. 网络隔离

- **默认 deny-all**（`00-namespace.yaml::default-deny-all`）
- **逐条 allow**（`70-network-policies.yaml`）：
  - DNS（kube-dns） + 上游 LLM API（出公网）
  - LiteLLM 仅接 3 Agent + Orchestrator
  - 3 Agent 仅接 Orchestrator
  - 3 MCP 仅接 3 Agent
  - Open WebUI 接 Ingress Controller
  - Langfuse Web 接 3 Agent + LiteLLM + Orchestrator

### 5. 滚动升级

- **liteLLM / 3 Agent / Orchestrator / Langfuse Web**：`RollingUpdate` + `maxSurge=1, maxUnavailable=0`
- **Postgres / ClickHouse / Redis / MinIO / Open WebUI**：`Recreate`（单实例 + PVC）
- **terminationGracePeriodSeconds** = 30-60s（OTEL span flush 完）
- **PDB**（`80-pdb.yaml`）：关键服务至少 1 副本可用

### 6. 镜像策略

| 镜像 | 拉取策略 | 说明 |
|---|---|---|
| 自研（litellm / agent / orchestrator / mcp） | `IfNotPresent` | 锁版本，CI 推送 |
| Open WebUI | `Always` | 上游 main 频繁更新 |
| 官方镜像（postgres / clickhouse / redis / minio / langfuse） | `IfNotPresent` | 锁 SemVer（不要用 :latest） |

## 与 docker-compose 的差异

| 维度 | docker-compose | K8s |
|---|---|---|
| 服务发现 | docker network + service 名 | Service + ClusterIP DNS |
| 配置注入 | `.env.prod` | ConfigMap + Secret |
| 持久化 | bind mount / volume | PVC（动态或静态） |
| 健康检查 | docker healthcheck | startup + liveness + readiness |
| 滚动升级 | 手动 / 限制 | RollingUpdate + PDB |
| 网络隔离 | docker network | NetworkPolicy |
| Secret | `.env.prod`（明文文件） | K8s Secret（base64 in etcd）|

## 生产化 TODO（P6 之后）

详见 [docs/MIGRATION.md §3](../../docs/MIGRATION.md)：

- [ ] 替换 `secrets.yaml` 为 Sealed Secrets / SOPS / External Secrets
- [ ] Ingress Controller（nginx / traefik / istio）
- [ ] cert-manager（自动签 TLS 证书）
- [ ] Prometheus + Grafana（指标采集）
- [ ] Loki（log 聚合）
- [ ] ArgoCD / Flux（GitOps 部署）
- [ ] Velero（备份 PVC）
- [ ] Kyverno / OPA（策略校验）

## 验证清单（CI 应自动跑）

1. **YAML 语法**：`kubectl apply --dry-run=client -k .`
2. **资源配额**：`kubectl -n a2a-prod get resourcequota`
3. **NetworkPolicy**：`kubectl -n a2a-prod get networkpolicy`（应至少 7 条）
4. **健康检查**：`kubectl -n a2a-prod get pods -o wide`（应全部 Running）
5. **OpenAI 兼容**：`curl /v1/models`（应返回 3 个 Agent + `auto`）
6. **Trace 通路**：调一次 `/v1/chat/completions`，去 Langfuse Dashboard 看到对应 trace
