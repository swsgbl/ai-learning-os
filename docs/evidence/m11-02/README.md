# M11-02 生产就绪证据 — RC 链路 + 只读基线 + 切换演练

采集方式：GitHub Actions 运行记录只读查询；本机生产近似栈 HTTP/DB 只读基线采集。全程零生产写入，未执行迁移/治理/锚定/部署，未读取或输出任何密钥值。
采集日期：2026-09-05；分支：`feature/m11-02-production-evidence`。

**结论：production_ready=false。** RC 流水线成功不等于生产就绪。

## RC 构建链路

| 阶段 | Run | 提交 | 结果 |
| --- | --- | --- | --- |
| main RC（首次） | 33938835814 | c6ba0d3f46ef44375331ed80a485367eb0a9c62e | 失败：compose project name 含点（`aios-rc-v0.1.0`） |
| 修复 | — | 794a76a8723bd75e688cd00cfefe3a7a782e044a | RC builder 内版本号映射去点：`v0.1.0` → `aios-rc-v0-1-0` |
| 分支 RC | 33939403335 | feature 分支（含修复） | 全部步骤成功 |
| main CI | 33940183843 | 001ea26b8eef308739e30f096a623582ded31771 | Web / API / Docker 三 job 全部 success |
| main RC（修复后） | 33940204511 | 001ea26b8eef308739e30f096a623582ded31771 | 全绿 |

### RC 产物

| 来源 | Run | Artifact | 大小（bytes） | sha256 | 过期 |
| --- | --- | --- | --- | --- | --- |
| 分支 RC | 33939403335 | release-candidate-v0.1.0 | 189052548 | ff8ba4272a9eba2c105fcdd1fbc53c085d01b6d28c526d1998fc0e3508480f10 | 2026-09-19 |
| main RC | 33940204511 | release-candidate-v0.1.0 | 189054719 | 5c228fd705c5641e181ddd87f2e8cbe43c23bc32cf229cd21e238ce45b191db9 | 2026-09-19 |

## 只读基线

### 遗留试卷（legacy papers）

| 指标 | 值 |
| --- | --- |
| total | 792 |
| referenced | 790 |
| unreferenced | 2 |
| total_questions | 1487 |
| keep_public | 790 |
| assign_owner | 2 |
| export_review | 0 |

### 草稿归属（draft ownership）

总数 6：course-generation 3 / variant-question 3。

### 迁移前 preflight（pre）

pass=1 / pending=4 / fail=0。

### 审计链（audit-chain）

valid=false；entries=0；audit_rows=0。
原因：`audit_chain_entries` 与 `audit_chain_state` 表缺失（迁移 0027 未执行）。

## Provider 冒烟

| Provider | 状态 | 缺失/说明 |
| --- | --- | --- |
| search | not_executed | 缺 `SEARCH_CLOUD_ENDPOINT` |
| llm | not_executed | 缺 `LLM_ENDPOINT` 与 `LLM_MODEL`；宿主存在来源不明的 `LLM_API_KEY`，不得使用 |
| cloud voice | not_executed | 缺 ASR/TTS 六个配置槽位与 `ASR_SMOKE_AUDIO`，且无真实 WAV 音频 |

## 切换演练（cutover rehearsal）

当前 rehearsal_ready=false。

| 状态 | 项 |
| --- | --- |
| pass | ci-main、preflight-pre-migration |
| pending | legacy-papers、draft-ownership |
| blocked | audit-chain-verify |
| not_executed | release-check、search-smoke、cloud-voice-smoke、llm-smoke、backup-restore、preflight-post-migration、audit-chain-anchor、cutover-approval |

注：`release-check --local-only` 曾被工具超时截断且无有效输出，因此保持 not_executed，不得记为 pass。

## 结论

- production_ready=false；RC 构建成功不等于生产就绪。
- 未执行：生产迁移、治理、锚定、部署、tag、Release、镜像推送。
