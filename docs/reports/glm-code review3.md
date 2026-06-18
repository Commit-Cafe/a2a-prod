# a2a-prod Code Review #3（GLM → MiniMax 修复第三轮）

> 审查人：GLM-5.2（技术总监 / 代码审查）
> 审查日期：2026-06-18（第三轮）
> 审查范围：MiniMax-M3 对 [glm-code review2.md](glm-code%20review2.md) 的全部修复（R1 / R2 / M1 + 防回归测试）
> 验证方式：源码逐行核对 + `pytest tests/contract/` 全量执行（**514 passed, 1 skipped**）
> 交付物：本报告 → MiniMax 修掉 R3（1 个 K8s 残留 + 1 个测试补漏）后即可完结

---

## 0. 总体评价

**review2 的 R1 / M1 完全修复，R2 修了 2/3，防回归测试也按建议补齐了**（且写得比我还细——`test_orchestrator_env_names_align_with_executor_getenv` 连端口号 12001/2/3 都交叉校验了，这是真正的"永久守住"的写法，值得点赞）。514 个契约测试全绿，比上轮还多 3 个（就是新加的对齐测试）。

**但 R2 留了一个尾巴**：`MiniMax_API_KEY` / `MiniMax_API_BASE` 的"统一全大写"改名在 3 个文件里只改了 2 个——`02-configmap.yaml` 和 `secrets.yaml.example` 改了，**唯独 `10-litellm.yaml`（真正注入 env 给 litellm 容器的 Deployment）漏改了**。后果是 K8s 下 MiniMax Agent 路由失败。而且我上轮建议的两个防回归测试之一（env 名对齐）恰好**没覆盖到 litellm 这条链路**，所以测试全绿也没抓到。

**结论：差最后一口气。** R3 是 1 行 yaml 改名 + 1 个测试补强（约 15 行测试代码），修完跑一遍 `pytest tests/contract/` 全绿我就给 **PASS**，项目完结。docker-compose 路径从 review2 起就一直 clean，本轮无变化。

---

## ✅ review2 修复确认

| 项 | 状态 | 验证依据 |
|---|---|---|
| **R1** orchestrator env 名 | ✅ 已修复 + 防回归 | `30-orchestrator.yaml:88-93` 三 env 名改为 `AGENT_URLS_GLM_AGENT` / `AGENT_URLS_DEEPSEEK_AGENT` / `AGENT_URLS_MINIMAX_AGENT`，值 `http://<svc>:1200X/`（与 `executor.py:41-43` 的 `os.getenv` key 一字不差）；`test_k8s_manifests.py:484-541` 新增 `test_orchestrator_env_names_align_with_executor_getenv`，**连端口号都断言**（532-534 行）。 |
| **R2** litellm config model_name | ✅ 已修复（configmap 侧） | `02-configmap.yaml:26` `glm-5`（与 agent + compose 三方对齐）；`test_k8s_manifests.py:462-481` `test_litellm_k8s_config_model_names_align_with_agents` 守护。 |
| **M1** kustomization 注释矛盾 | ✅ 已修复 + 防回归 | `kustomization.yaml:32-36` 注释改为"实际**在** resources 列表中，apply -k 前必须先 cp"；`test_k8s_manifests.py:543+` `test_kustomization_secrets_yaml_comment_is_consistent` 守护。 |
| **防回归** secrets key 清单 | ✅ 已加 | `test_k8s_manifests.py:386-413` `test_secrets_example_has_all_required_keys` 显式要求 `MINIMAX_API_KEY` / `MINIMAX_API_BASE`。 |
| **测试回归** | ✅ 无回归 | `pytest tests/contract/` → **514 passed, 1 skipped**（比上轮 +3，即新加的对齐测试）。 |

防回归测试的设计质量超出预期——`test_orchestrator_env_names_align_with_executor_getenv` 是从 `executor.py` 源码里**正则提取** `os.getenv("AGENT_URLS_...")` 的 key，再和 yaml 注入的 env 名交叉校验，不是硬编码字符串比对。这种写法以后改任一边都会被测试抓住，是教科书级的防回归。

---

## 🔴 新发现的 Blocker（R2 改名没改干净）

### R3. `10-litellm.yaml` 仍注入 `MiniMax_API_KEY` / `MiniMax_API_BASE`，K8s 下 MiniMax Agent 路由失败

**位置**：`infra/k8s/10-litellm.yaml:97-106`

**问题（Why）**：
R2 把 env 名统一全大写，改了两个文件，漏了第三个：

| 文件 | 改前 | 改后 | 状态 |
|---|---|---|---|
| `02-configmap.yaml`（litellm config `os.environ/...`） | `MiniMax_API_KEY` | `MINIMAX_API_KEY` | ✅ 已改（`:40`） |
| `secrets.yaml.example`（Secret stringData key） | `MiniMax_API_KEY` | `MINIMAX_API_KEY` | ✅ 已改（`:36-37`） |
| **`10-litellm.yaml`（Deployment env 注入）** | `MiniMax_API_KEY` | **仍是 `MiniMax_API_KEY`** | ❌ **漏改** |

当前 `10-litellm.yaml:97-106`：
```yaml
- name: MiniMax_API_KEY              # ← 注入的 env 叫 MiniMax_API_KEY
  valueFrom:
    secretKeyRef:
      name: a2a-prod-secrets
      key: MiniMax_API_KEY           # ← 但 Secret 里这个 key 已不存在（改叫 MINIMAX_API_KEY 了）
- name: MiniMax_API_BASE
  valueFrom:
    secretKeyRef:
      name: a2a-prod-secrets
      key: MiniMax_API_BASE          # ← 同上，Secret 里找不到 → 注入空值
```

后果链（K8s 路径）：
1. litellm 容器拿到的 env 是 `MiniMax_API_KEY`（不是 `MINIMAX_API_KEY`），且因 Secret 里 `MiniMax_API_KEY` 这个 key 已被删（改名了），实际值为空。
2. MiniMax Agent 发请求 `model=openai/MiniMax-M3` → litellm 按 `02-configmap.yaml:40` 读 `os.environ/MINIMAX_API_KEY` → **这个 env 根本没被注入**（注入的是 `MiniMax_API_KEY`）→ 空 key → MiniMax 提供商返回 401。
3. **K8s 下 MiniMax Agent（主力码农）完全不可用**，SDLC 工作流的"MiniMax 写码"环节全挂。

> 为什么 GLM/DeepSeek 没事：`10-litellm.yaml:77-96` 的 `GLM_API_KEY`/`GLM_API_BASE`/`DEEPSEEK_API_KEY`/`DEEPSEEK_API_BASE` 本来就是全大写，R2 没动它们，所以对齐。只有 MiniMax 这条线改名不彻底。

**为什么测试没抓到**：`test_secrets_example_has_all_required_keys` 只查 `secrets.yaml.example` 有没有 `MINIMAX_API_KEY`（有，过了）；`test_litellm_k8s_config_model_names_align_with_agents` 只查 model_name（`MiniMax-M3` 对齐，过了）。**没有任何测试把"`10-litellm.yaml` 注入的 env 名"和"`02-configmap.yaml` 里 `os.environ/` 引用的 env 名"对齐**——这正是 R3 的盲区。

**建议修复**：
1. 把 `10-litellm.yaml:97-106` 四处 `MiniMax_` 改成 `MINIMAX_`：
```yaml
- name: MINIMAX_API_KEY
  valueFrom:
    secretKeyRef:
      name: a2a-prod-secrets
      key: MINIMAX_API_KEY
- name: MINIMAX_API_BASE
  valueFrom:
    secretKeyRef:
      name: a2a-prod-secrets
      key: MINIMAX_API_BASE
```

2. **补一个防回归测试**，把 litellm 这条链路也守住（参考 `test_orchestrator_env_names_align_with_executor_getenv` 的写法）：解析 `02-configmap.yaml` 里 litellm config 所有 `os.environ/<KEY>` 引用，得到"litellm 运行时需要的 env 名集合"；再解析 `10-litellm.yaml` 的 Deployment env 注入，得到"实际注入的 env 名集合"；断言前者 ⊆ 后者。伪代码：
```python
def test_litellm_deployment_envs_cover_config_references() -> None:
    """R3 防回归：10-litellm.yaml 注入的 env 必须覆盖 02-configmap.yaml
    litellm config 里所有 os.environ/<KEY> 引用。防 MiniMax_ vs MINIMAX_ 改名不彻底。"""
    needed = _litellm_config_env_refs()        # 从 02-configmap.yaml os.environ/ 提取
    injected = _litellm_deployment_env_names()  # 从 10-litellm.yaml env[].name 提取
    missing = needed - injected
    assert not missing, f"10-litellm.yaml 未注入 litellm config 引用的 env: {missing}"
```
这个测试能让"configmap 改了 env 名但 deployment 没同步"这类问题永久绝迹——和 orchestrator 那条防线对称。

---

## 🟡 次要问题（应修，非阻塞）

### M2. `docs/MIGRATION.md` 和 `docs/SPEC.md` 仍写 `MiniMax_API_KEY`

**位置**：
- `docs/MIGRATION.md:210` `| GLM_API_KEY / DEEPSEEK_API_KEY / MiniMax_API_KEY | P0 | ... |`
- `docs/SPEC.md:599` `GLM_API_KEY / DEEPSEEK_API_KEY / MiniMax_API_KEY`

**问题**：R2 把代码侧统一成全大写了，但这两处文档还在写 `MiniMax_API_KEY`（混合大小写）。不影响运行，但和实际配置不一致，会误导后续部署的人。

**建议**：把这两处的 `MiniMax_API_KEY` 改成 `MINIMAX_API_KEY`（和代码、secrets.yaml.example 一致）。顺手 grep 一遍全仓 `MiniMax_API` 确保没有第 4 处。

---

## 回归检查清单（MiniMax 修复后请逐项确认）

- [ ] **R3**：`grep -rn "MiniMax_API" infra/` 应只在**注释**里出现（`02-configmap.yaml` / `secrets.yaml.example` / `10-litellm.yaml` 的溯源注释），代码/配置值零残留；`10-litellm.yaml:97-106` 四处全大写。
- [ ] **R3 防回归测试**：新增 `test_litellm_deployment_envs_cover_config_references`，能抓住"configmap 引用 `MINIMAX_API_KEY` 但 deployment 注入 `MiniMax_API_KEY`"这类错配（临时把 `10-litellm.yaml` 改回 `MiniMax_` 应让测试红，改回全大写应让测试绿——用这个反向验证测试有效）。
- [ ] **M2**：`grep -rn "MiniMax_API" docs/` 为空。
- [ ] 全量回归：`.venv-prod/Scripts/python -m pytest tests/contract/ -q` 全绿（含新增的 litellm env 对齐测试）。

---

## 结语

连续三轮下来，review1 的 3 个 Blocker、review2 的 2 个回归都修得很扎实，防回归测试也越来越体系化（从纯结构检查 → 跨模块语义对齐 → 这次再补 litellm env 链路就齐了）。R3 是整个 review 过程里**最小的一刀**：4 行 yaml 改名 + 1 个对称的防回归测试，修完跑绿测试就完结。

docker-compose 路径已经 clean 三轮了；K8s 路径就差 `10-litellm.yaml` 这一处 MiniMax 改名。下一轮全绿，我直接判 PASS，项目完结。

— GLM-5.2
