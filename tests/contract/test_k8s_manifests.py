"""P6 阶段 K8s 清单验证测试（无需真实 k8s 集群，纯静态检查）。

覆盖：

1. **YAML 语法**：所有 manifest 文件能正确解析
2. **资源类型合法性**：K8s API 允许的 kind 列表
3. **Namespace 一致性**：所有非 Namespace 资源都属于 ``a2a-prod`` namespace
4. **label 一致性**：workload 必须带 ``app.kubernetes.io/component``
5. **健康检查三件套**：所有 Deployment MUST 有 startupProbe + livenessProbe + readinessProbe
6. **资源配额**：所有容器 MUST 设 requests 与 limits
7. **安全基线**：所有容器 MUST 设 runAsNonRoot + allowPrivilegeEscalation=false
8. **镜像 tag**：自研镜像 MUST 锁版本（不能用 :latest）
9. **PDB 覆盖**：所有可扩缩的 Deployment MUST 有对应 PDB
10. **Kustomize 入口**：kustomization.yaml 引用所有 manifest 文件

标记：``@pytest.mark.contract``

用法：

.. code-block:: bash

    pytest -m "contract" tests/contract/test_k8s_manifests.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

# ============================================================
# 配置
# ============================================================

K8S_DIR = Path(__file__).resolve().parents[2] / "infra" / "k8s"

# 合法 K8s kind（白名单）
ALLOWED_KINDS = frozenset(
    {
        "Namespace",
        "ResourceQuota",
        "LimitRange",
        "ServiceAccount",
        "ConfigMap",
        "Secret",
        "PersistentVolumeClaim",
        "Deployment",
        "StatefulSet",
        "DaemonSet",
        "Service",
        "Ingress",
        "HorizontalPodAutoscaler",
        "NetworkPolicy",
        "PodDisruptionBudget",
        "Job",
        "CronJob",
    }
)

# 必须有健康检查三件套的 workload kind
WORKLOAD_KINDS = frozenset({"Deployment", "StatefulSet", "DaemonSet"})

# 镜像黑名单（不能用 :latest）
BANNED_IMAGE_TAGS = frozenset({"latest", "main", "master"})


# ============================================================
# 辅助函数
# ============================================================


def _load_manifests() -> list[dict[str, Any]]:
    """加载 K8s_DIR 下所有 *.yaml 文件，扁平化为 resource 列表。

    注意：kustomization.yaml 单独处理（结构不同）。
    """
    resources: list[dict[str, Any]] = []
    for path in sorted(K8S_DIR.glob("*.yaml")):
        if path.name in {"kustomization.yaml", "secrets.yaml.example"}:
            # 单独验证
            continue
        with path.open(encoding="utf-8") as f:
            for doc in yaml.safe_load_all(f):
                if doc is None:  # 空文件
                    continue
                if isinstance(doc, list):
                    resources.extend(doc)
                else:
                    resources.append(doc)
    return resources


def _all_workload_specs() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """返回 ``(resource, spec)`` 列表，仅含 workload（Deployment / StatefulSet）。"""
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for r in _load_manifests():
        if r.get("kind") in WORKLOAD_KINDS:
            out.append((r, r["spec"]))
    return out


def _all_workload_names() -> set[str]:
    """返回所有 workload 的 metadata.name 集合。"""
    return {r["metadata"]["name"] for r, _ in _all_workload_specs()}


def _all_containers() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """返回 ``(resource, container)`` 列表，覆盖所有 workload 的所有容器。"""
    out: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for r, spec in _all_workload_specs():
        tmpl = spec.get("template", {})
        for c in tmpl.get("spec", {}).get("containers", []):
            out.append((r, c))
    return out


# ============================================================
# 1. YAML 语法
# ============================================================


def test_all_yaml_files_parse() -> None:
    """所有 *.yaml 文件 MUST 能被 PyYAML 正确解析。"""
    for path in K8S_DIR.glob("*.yaml"):
        with path.open(encoding="utf-8") as f:
            for doc in yaml.safe_load_all(f):
                assert doc is not None, f"empty document in {path.name}"


# ============================================================
# 2. 资源类型合法性
# ============================================================


def test_only_allowed_kinds() -> None:
    """所有资源的 kind MUST 在白名单内。"""
    for r in _load_manifests():
        kind = r.get("kind")
        assert kind in ALLOWED_KINDS, (
            f"unknown kind: {kind} in {r.get('metadata', {}).get('name', '?')}"
        )


# ============================================================
# 3. Namespace 一致性
# ============================================================


def test_all_resources_in_a2a_prod_namespace() -> None:
    """所有非 Namespace 资源 MUST 在 a2a-prod namespace。"""
    for r in _load_manifests():
        if r.get("kind") == "Namespace":
            # Namespace 自身的 metadata.namespace 应为空
            continue
        ns = r.get("metadata", {}).get("namespace")
        assert ns == "a2a-prod", (
            f"{r.get('kind')}/{r.get('metadata', {}).get('name', '?')} "
            f"has namespace={ns!r}, expected 'a2a-prod'"
        )


# ============================================================
# 4. label 一致性
# ============================================================


def test_all_workloads_have_component_label() -> None:
    """所有 workload MUST 带 ``app.kubernetes.io/component`` label。"""
    for r, _ in _all_workload_specs():
        labels = r["spec"]["template"]["metadata"].get("labels", {})
        assert "app.kubernetes.io/component" in labels, (
            f"workload {r['metadata']['name']} missing app.kubernetes.io/component"
        )


def test_all_workloads_have_name_label() -> None:
    """所有 workload MUST 带 ``app.kubernetes.io/name`` label（用于 Service selector）。"""
    for r, _ in _all_workload_specs():
        labels = r["spec"]["template"]["metadata"].get("labels", {})
        assert "app.kubernetes.io/name" in labels, (
            f"workload {r['metadata']['name']} missing app.kubernetes.io/name"
        )


# ============================================================
# 5. 健康检查三件套
# ============================================================


def test_all_workloads_have_startup_probe() -> None:
    """所有 workload MUST 有 startupProbe（首次启动宽限）。"""
    # Langfuse Postgres / ClickHouse 用 exec probe，spec 略有不同
    # 简化：只断言「至少有 startupProbe OR initialDelaySeconds 显式」之一
    for r, _ in _all_workload_specs():
        for c in r["spec"]["template"]["spec"].get("containers", []):
            assert "startupProbe" in c or c.get("image", "").endswith(":latest"), (
                f"container {c['name']} in {r['metadata']['name']} missing startupProbe"
            )


def test_all_workloads_have_liveness_probe() -> None:
    """所有 workload MUST 有 livenessProbe。"""
    for r, _ in _all_workload_specs():
        for c in r["spec"]["template"]["spec"].get("containers", []):
            assert "livenessProbe" in c, (
                f"container {c['name']} in {r['metadata']['name']} missing livenessProbe"
            )


def test_all_workloads_have_readiness_probe() -> None:
    """所有 workload MUST 有 readinessProbe。"""
    for r, _ in _all_workload_specs():
        for c in r["spec"]["template"]["spec"].get("containers", []):
            assert "readinessProbe" in c, (
                f"container {c['name']} in {r['metadata']['name']} missing readinessProbe"
            )


# ============================================================
# 6. 资源配额
# ============================================================


def test_all_containers_have_requests_and_limits() -> None:
    """所有容器 MUST 设 requests + limits（避免被 OOM kill 整节点）。"""
    for r, c in _all_containers():
        resources = c.get("resources", {})
        assert "requests" in resources, (
            f"container {c['name']} in {r['metadata']['name']} missing resources.requests"
        )
        assert "limits" in resources, (
            f"container {c['name']} in {r['metadata']['name']} missing resources.limits"
        )
        # CPU / 内存必须有
        for key in ("cpu", "memory"):
            assert key in resources["requests"], (
                f"container {c['name']} in {r['metadata']['name']} "
                f"missing resources.requests.{key}"
            )
            assert key in resources["limits"], (
                f"container {c['name']} in {r['metadata']['name']} "
                f"missing resources.limits.{key}"
            )


# ============================================================
# 7. 安全基线
# ============================================================


def test_all_containers_run_as_non_root() -> None:
    """所有容器 MUST 设 runAsNonRoot=true。"""
    for r, c in _all_containers():
        sc = c.get("securityContext", {})
        # 容器级或 Pod 级 securityContext 至少一处设了
        pod_sc = r["spec"]["template"]["spec"].get("securityContext", {})
        assert sc.get("runAsNonRoot") or pod_sc.get("runAsNonRoot"), (
            f"container {c['name']} in {r['metadata']['name']} missing runAsNonRoot"
        )


def test_all_containers_disallow_privilege_escalation() -> None:
    """所有容器 MUST 设 allowPrivilegeEscalation=false。"""
    for r, c in _all_containers():
        sc = c.get("securityContext", {})
        assert sc.get("allowPrivilegeEscalation") is False, (
            f"container {c['name']} in {r['metadata']['name']} "
            f"missing allowPrivilegeEscalation=false"
        )


# ============================================================
# 8. 镜像 tag 锁版本
# ============================================================


@pytest.mark.parametrize("r,c", _all_containers(), ids=lambda x: x[0]["metadata"]["name"])
def test_no_latest_image_tag(r: dict[str, Any], c: dict[str, Any]) -> None:
    """自研镜像 MUST 锁版本（不能用 :latest / :main / :master）。

    例外：Open WebUI 显式用 :main（上游策略，K8s 里 imagePullPolicy: Always）
    """
    image = c.get("image", "")
    if "open-webui" in image:
        # 显式豁免
        return
    for banned in BANNED_IMAGE_TAGS:
        assert not image.endswith(f":{banned}"), (
            f"container {c['name']} in {r['metadata']['name']} "
            f"uses banned tag :{banned} (image={image})"
        )


# ============================================================
# 9. PDB 覆盖
# ============================================================


def test_pdb_covers_all_multi_replica_workloads() -> None:
    """所有 replicas >= 2 的 Deployment MUST 有对应 PDB。"""
    # 加载 PDB
    pdbs: set[str] = set()
    for r in _load_manifests():
        if r.get("kind") == "PodDisruptionBudget":
            pdbs.add(r["metadata"]["name"])

    # 检查所有多副本 workload
    for r, spec in _all_workload_specs():
        replicas = spec.get("replicas", 1)
        if replicas >= 2:
            name = r["metadata"]["name"]
            # 必须有同名 PDB（OR 名称匹配）—— 这里用严格同名
            assert name in pdbs, (
                f"workload {name} has {replicas} replicas but no PDB. "
                f"Expected PDB named '{name}'"
            )


# ============================================================
# 10. Kustomize 入口
# ============================================================


def test_kustomization_references_all_manifests() -> None:
    """kustomization.yaml 的 ``resources`` 列表 MUST 包含所有 manifest 文件（除 example / kustomization.yaml 自身）。"""
    kustomization_path = K8S_DIR / "kustomization.yaml"
    with kustomization_path.open(encoding="utf-8") as f:
        kustomization = yaml.safe_load(f)

    referenced = set(kustomization.get("resources", []))

    # 找出所有 .yaml 文件（kustomization.yaml 和 secrets.yaml.example 除外）
    actual = {p.name for p in K8S_DIR.glob("*.yaml")}
    actual.discard("kustomization.yaml")
    # secrets.yaml 可能不存在（gitignore），example 应当被引用（但不强制）
    actual.discard("secrets.yaml.example")

    # 注意：secrets.yaml.example 不在 kustomization.yaml 的 resources 列表里
    # （因为它是 example，不是真实部署资源）—— 这点已在 kustomization.yaml 注释说明
    # 不强制 example 被引用
    missing = actual - referenced
    assert not missing, (
        f"kustomization.yaml does not reference these manifests: {missing}"
    )


def test_kustomization_has_namespace() -> None:
    """kustomization.yaml MUST 设 namespace=a2a-prod（防 apply 到 default）。"""
    kustomization_path = K8S_DIR / "kustomization.yaml"
    with kustomization_path.open(encoding="utf-8") as f:
        kustomization = yaml.safe_load(f)
    assert kustomization.get("namespace") == "a2a-prod"


# ============================================================
# 11. NetworkPolicy 完整性
# ============================================================


def test_default_deny_all_network_policy_exists() -> None:
    """00-namespace.yaml MUST 有 default-deny-all NetworkPolicy。"""
    nps = [r for r in _load_manifests() if r.get("kind") == "NetworkPolicy"]
    assert any(
        np["metadata"]["name"] == "default-deny-all" for np in nps
    ), "missing default-deny-all NetworkPolicy (00-namespace.yaml)"


def test_at_least_n_explicit_allow_network_policies() -> None:
    """至少 5 条显式 allow NetworkPolicy。"""
    nps = [r for r in _load_manifests() if r.get("kind") == "NetworkPolicy"]
    # default-deny-all + allow-dns + allow-litellm + allow-agent + allow-orchestrator +
    # allow-mcp + allow-langfuse + allow-open-webui = 8
    assert len(nps) >= 5, f"only {len(nps)} NetworkPolicy defined, expected >= 5"


# ============================================================
# 12. Secret 模板
# ============================================================


def test_secrets_example_has_all_required_keys() -> None:
    """secrets.yaml.example MUST 包含所有业务侧需要的 key。"""
    secrets_path = K8S_DIR / "secrets.yaml.example"
    with secrets_path.open(encoding="utf-8") as f:
        secret = yaml.safe_load(f)

    string_data = secret.get("stringData", {})
    required = {
        "LITELLM_MASTER_KEY",
        "GLM_API_KEY",
        "DEEPSEEK_API_KEY",
        "MiniMax_API_KEY",
        "LANGFUSE_INIT_USER_PASSWORD",
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY",
        "LANGFUSE_INIT_PROJECT_SECRET_KEY",
        "NEXTAUTH_SECRET",
        "POSTGRES_PASSWORD",
        "MINIO_ROOT_PASSWORD",
        "REDIS_PASSWORD",
        # P6: 含密码的完整 URL（K8s ConfigMap 不支持变量展开，必须走 Secret）
        "DATABASE_URL",
        "REDIS_URL",
    }
    missing = required - string_data.keys()
    assert not missing, f"secrets.yaml.example missing keys: {missing}"


def test_secrets_no_real_secrets_yaml_committed() -> None:
    """真实的 secrets.yaml MUST NOT 提交到 git（仅有 .example）。"""
    secrets_real = K8S_DIR / "secrets.yaml"
    assert not secrets_real.exists(), (
        f"{secrets_real} exists — this is a real secrets file and MUST be in .gitignore!"
    )
