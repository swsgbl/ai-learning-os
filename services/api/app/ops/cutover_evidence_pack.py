"""M10-16 cutover evidence pack：生产切换证据包脚手架与操作手册（本地生成）。

定位：与 cutover-rehearsal（M10-15，切换时间线 13 步只读编排器）配套——
rehearsal 回答「证据齐不齐、绑没绑」，本工具回答「每份证据从哪来、怎么
脱敏、怎么算通过、审批哈希怎么算」：在运维显式指定的 artifacts/temp
新目录内生成 13 步证据**模板**（`.template.json` 命名，预填值全部
REPLACE-ME 形态）+ 锚文件副本模板 + 一份逐项操作手册 README，并提供
approval-draft 本地哈希底稿（仍为 DRAFT，须人工逐项确认）。

安全边界（docs/DEVELOPMENT.md「生产切换证据包脚手架（M10-16）」节同口径）：

- **不执行任何生产操作**：不迁移、不锚定、不清理、不备份、不部署、不
  启停服务、不发布、不回滚——命令没有 --yes 执行形态；手册内出现的一切
  生产命令（production-preflight / backup / audit-chain-* / *-migrate 等）
  都由运维**人工逐项授权后手动执行**，本工具只是文档与模板的生成器；
- 只读本地：不连接数据库、不调用 API、不访问网络、不读取任何环境变量
  （`os.environ` 零引用；路径护栏复用 legacy 报告的 is_safe_artifact_path，
  其内部 git check-ignore 子进程只做忽略判定，不消费任何密钥）；
- scaffold 只写入用户显式指定、且通过 artifacts/temp gitignore 护栏的
  **新目录**：路径任何已存在组件是 symlink 即拒绝（fail-closed）、目标
  是 artifacts/temp 目录本身即拒绝、已存在且非空则必须与本工具脚手架
  字节一致（幂等重放）否则拒绝——绝不覆盖任何既有文件；
- 模板防误用（三层）：(1) `.template.json` / `.jsonl.template` 命名——
  cutover-rehearsal 只认精确证据文件名（ci-main.json 等），模板只会进
  unrecognized_files，不可能被当作证据评估；(2) 预填值全部是 REPLACE-ME
  字符串（类型/枚举全错），即使手工改名也只会得到 blocked，不会被误判
  为 pass；(3) 每个模板带 DRAFT/REPLACE-ME 显著标注与 `_template_notice`
  说明字段；
- approval-draft 是**草稿底稿**不是审批记录：输出固定带 `draft: true`、
  人工必填字段清单（manual_fields_required，全部 REPLACE-ME 提示）与
  「直接改名只会 blocked」声明；误用即 malformed => blocked，不构成
  生产放行证据；
- 输出零敏感、零生产业务 ID：模板与手册只有字段名与占位说明；approval
  底稿只含 SHA-256 与白名单问题摘要（敏感键/内嵌凭据命中只报字段路径，
  值从不回显），最终经 evidence_kit.scrub_sensitive 兜底。

手册与模板同源：13 步的来源命令/脱敏要求/通过失败语义/人工授权要求/
隔离 fixture 形态都登记在本模块 MANUAL_ENTRIES / FIXTURE_FORMS（step
矩阵本身复用 cutover_rehearsal.STEPS 单一事实源），README 由数据渲染
生成，测试用同一份数据守卫「无漏项」——新增步骤时漏更新手册会直接红。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.ops.cutover_rehearsal import (
    ANCHOR_COMPANION_FILE,
    BINDABLE_SUPPORTING_FILES,
    STAGE_TITLES,
    STAGES,
    STEPS,
)
from app.ops.evidence_kit import (
    check_evidence_dir,
    check_regular_file,
    find_embedded_credential,
    find_sensitive_key,
    load_json_object,
    scrub_sensitive,
    sha256_file,
)
from app.ops.legacy_papers import is_safe_artifact_path

#: 脚手架固定产物：13 步模板 + 锚文件副本模板 + 手册
README_FILE = "README.md"
TEMPLATE_NOTICE = (
    "DRAFT/REPLACE-ME：这是脚手架模板，不是证据——填入真实值并改名为对应"
    "证据文件名（见 _target_file）后才成为证据候选。模板预填值全部是"
    "REPLACE-ME 形态（类型/枚举故意不符），直接改名使用只会得到 blocked；"
    "本工具不执行任何生产操作，真实生产操作必须人工逐项授权。"
)
APPROVAL_DRAFT_NOTICE = (
    "DRAFT（草稿底稿）：本文件只是哈希底稿与人工确认清单，不是审批记录——"
    "直接改名为 cutover-approval.json 只会得到 blocked。审批人必须逐项人工"
    "确认每份证据内容，自行填写 manual_fields_required 列出的全部字段并核对"
    "哈希后，才能构成 cutover-approval.json；任何真实生产操作仍须人工逐项"
    "授权执行。"
)
_COMMON_AUTHORIZATION = (
    "真实生产操作必须运维/审批人逐项显式授权后人工执行；本工具与 "
    "cutover-rehearsal 均不代为执行任何生产命令"
)


class PackInputError(Exception):
    """脚手架目标目录/路径护栏问题（CLI 退出码 2：未写入任何文件）。"""


# --- 模板 -----------------------------------------------------------------------


def template_name(evidence_file: str) -> str:
    """证据文件名 -> 模板文件名（ci-main.json => ci-main.template.json）。"""
    if evidence_file.endswith(".json"):
        return evidence_file[: -len(".json")] + ".template.json"
    return evidence_file + ".template"


#: 各步模板字段与取值提示（键=评估器要求的字段名；预填值一律 REPLACE-ME 字符串，
#: 类型/枚举故意不符——改名直用必 blocked；嵌套对象内同样是 REPLACE-ME 提示）。
TEMPLATE_FIELDS: dict[str, dict[str, Any]] = {
    "ci-main": {
        "run_id": "整数（>=1）：远端 CI run id",
        "merge_commit": "7-40 位十六进制：发布 merge commit",
        "conclusion": "success | failure | cancelled | startup_failure | "
        "timed_out | action_required（仅 success 构成 pass）",
    },
    "release-check": {
        "all_green": "布尔：release-check 是否全绿",
        "total": "整数（>=1）：检查项总数",
        "passed": "整数：通过项数（不得大于 total）",
        "failed_ids": "字符串列表：未通过项 id（全绿时为空列表）",
    },
    "search-smoke": {
        "executed": "布尔：冒烟是否已执行",
        "result": "pass | fail | not_executed（冒烟结果）",
    },
    "cloud-voice-smoke": {
        "executed": "布尔：冒烟是否已执行",
        "result": "pass | fail | not_executed（冒烟结果）",
    },
    "llm-smoke": {
        "executed": "布尔：冒烟是否已执行",
        "result": "pass | fail | not_executed（冒烟结果）",
    },
    "legacy-papers": {
        "pending_count": "整数：待人工决策条数（进入切换窗口前必须归零）",
        "batches": "列表：分批执行记录，每项含 executed 布尔字段",
    },
    "draft-ownership": {
        "pending_count": "整数：待人工决策条数（进入切换窗口前必须归零）",
        "batches": "列表：分批执行记录，每项含 executed 布尔字段",
    },
    "preflight-pre-migration": {
        "phase": "pre-migration（必须与文件名声明的阶段一致）",
        "summary": {
            "pass": "整数：pass 计数",
            "pending": "整数：pending 计数",
            "fail": "整数：fail 计数（>0 即 blocked）",
            "not_configured": "整数：not_configured 计数",
        },
    },
    "backup-restore": {
        "schema_version": "aios-backup-v1（备份 manifest 版本，唯一合法值）",
        "created_at": "ISO 8601 时间：备份 manifest 创建时间",
        "restore_drill": {
            "verified": "布尔：隔离库恢复演练是否验证通过（false => pending）",
            "inserted_rows": "整数：演练回灌行数",
        },
    },
    "preflight-post-migration": {
        "phase": "post-migration（必须与文件名声明的阶段一致）",
        "summary": {
            "pass": "整数：pass 计数（放行形态要求 >0）",
            "pending": "整数：pending 计数（放行形态要求 0）",
            "fail": "整数：fail 计数（>0 即 blocked）",
            "not_configured": "整数：not_configured 计数（放行形态要求 0）",
        },
    },
    "audit-chain-verify": {
        "valid": "布尔：审计哈希链校验是否 valid（false => blocked）",
        "entries": "整数：链条目数",
    },
    "audit-chain-anchor": {
        "anchor": {
            "status": "up-to-date | valid | invalid（落后 => pending，"
            "invalid => blocked）",
            "anchors": "整数：锚点数（提供副本时必须与副本行数一致）",
        },
        "worm": {
            "archived": "布尔：锚文件是否已归档 WORM/对象锁/离线介质"
            "（false => pending）"
        },
    },
    "cutover-approval": {
        "schema_version": "整数：固定 1",
        "approved_at": "ISO 8601 时间：审批时间（人工填写）",
        "note": "非空字符串：审批说明（rehearsal 只验证非空，不回显）",
        "window": {
            "start": "ISO 8601 时间：切换窗口开始（须早于 end）",
            "end": "ISO 8601 时间：切换窗口结束",
        },
        "rollback_plan": "非空字符串：回滚计划（人工填写）",
        "observation": "非空字符串：观察期安排（人工填写）",
        "approved_by": "非空字符串：审批人记录（只做记录，不做身份认证）",
        "step_evidence": "对象：其余 12 步证据文件 sha256（64 位小写 hex），"
        "见 README「审批与 SHA-256 计算」",
        "supporting_evidence": "对象：当前实际存在的 supporting 文件（目前仅 "
        "audit-anchor.jsonl）的 sha256 mapping；目录没有副本时必须为空对象",
    },
}


def build_step_template(step_id: str) -> dict[str, Any]:
    """单步证据模板：step 自声明正确，其余字段一律 REPLACE-ME 字符串。

    预填值类型/枚举故意与评估器要求不符（run_id 给字符串、executed 给字符
    串、summary 计数给字符串……）——即使把模板改名为真实证据文件名，也只
    会在 req_int/req_bool/req_choice 处 MalformedEvidence => blocked，
    绝无 pass 读法。
    """
    fields = TEMPLATE_FIELDS[step_id]

    def to_placeholders(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: to_placeholders(item) for key, item in value.items()}
        return f"REPLACE-ME: {value}"

    return {
        "_template_notice": TEMPLATE_NOTICE,
        "_target_file": "填毕后改名为 " + step_id + ".json",
        "step": step_id,
        **{key: to_placeholders(value) for key, value in fields.items()},
    }


def build_anchor_companion_template() -> bytes:
    """锚文件副本模板（单行 JSONL）：改名为 audit-anchor.jsonl 后经
    load_anchor_file 校验必然 problems（缺 schema/algorithm/sequence/
    head_hash/anchor_hash 字段）=> blocked。"""
    line = {
        "_template_notice": TEMPLATE_NOTICE,
        "_target_file": (
            "填毕后改名为 audit-anchor.jsonl（须为自洽锚链，可用 "
            "audit-chain-anchor 工具真实产出后复制；演练 fixture 可不提供副本）"
        ),
    }
    return (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")

# --- 操作手册数据（13 步逐项：来源/脱敏/语义/授权/隔离 fixture） ------------------

#: 各步手册内容（step 矩阵/evidence 文件名/阶段归属复用 cutover_rehearsal.STEPS，
#: 不在本模块重复登记；测试守卫 MANUAL_ENTRIES 与 STEPS 一一对应无漏项）。
MANUAL_ENTRIES: dict[str, dict[str, str]] = {
    "ci-main": {
        "source": "远端 CI（GitHub Actions main 分支）：发布 merge commit 的 "
        "CI 全绿后，从运行记录人工抄录 run_id / merge_commit / conclusion"
        "（gh run view 或 Web 界面；本工具不自动抓取）",
        "redaction": "只含 run_id/merge_commit/conclusion 三个白名单标量；"
        "不含 CI 日志正文、actor、鉴权材料或任何内嵌凭据的 URL",
        "semantics": "conclusion=success => pass；其余 conclusion（failure/"
        "cancelled/startup_failure/timed_out/action_required）=> blocked"
        "（远端门禁未绿不得进入切换窗口）；无文件 => not_executed；"
        "结构不符/敏感键 => blocked",
        "authorization": _COMMON_AUTHORIZATION,
    },
    "release-check": {
        "source": "python -m app.ops.cli release-check --local-only"
        "（九字面 lint/typecheck/test/build/E2E/migration/backup/voice/"
        "license 全绿后导出汇总）",
        "redaction": "all_green/total/passed/failed_ids 四项；failed_ids 是 "
        "release-check 检查项 id（非生产业务 ID），不含任何密钥",
        "semantics": "all_green=true 且 passed=total、failed_ids 为空 => pass；"
        "未全绿 => blocked；无文件 => not_executed；计数自相矛盾/敏感键 => blocked",
        "authorization": "本地命令无生产副作用；结论进入证据前由发布负责人复核",
    },
    "search-smoke": {
        "source": "bash infra/smoke_search.sh（打真实 search 端点，"
        "SEARCH_CLOUD_API_KEY 可选——无鉴权 SearXNG 合法）后人工抄录脱敏结果",
        "redaction": "只含 executed/result；端点上的鉴权材料/Authorization 头"
        "不得进入证据（冒烟脚本输出本身已脱敏，可另行存档于 artifacts）",
        "semantics": "executed=true 且 result=pass => pass；result=fail => "
        "blocked；result=not_executed（未跑）=> not_executed；executed 与 "
        "result 矛盾/敏感键 => blocked",
        "authorization": _COMMON_AUTHORIZATION,
    },
    "cloud-voice-smoke": {
        "source": "运维执行 bash infra/smoke_voice_cloud.sh（部署 key + 真实"
        "短语音 WAV，ASR/TTS 双探针；key 不入库不入码不入证据）后人工抄录"
        "脱敏结果",
        "redaction": "只含 executed/result；部署 key、音频对象键、端点鉴权"
        "材料不得进入证据",
        "semantics": "executed=true 且 result=pass => pass；result=fail => "
        "blocked；result=not_executed => not_executed；矛盾/敏感键 => blocked",
        "authorization": _COMMON_AUTHORIZATION,
    },
    "llm-smoke": {
        "source": "运维以部署 key 冒烟 LLM 网关（bash infra/smoke_llm.sh 或"
        "等价探针，M10-01 网关真连通）后人工抄录脱敏结果",
        "redaction": "只含 executed/result；部署 key 与模型端点鉴权材料不得"
        "进入证据",
        "semantics": "executed=true 且 result=pass => pass；result=fail => "
        "blocked；result=not_executed => not_executed；矛盾/敏感键 => blocked",
        "authorization": _COMMON_AUTHORIZATION,
    },
    "legacy-papers": {
        "source": "python -m app.ops.cli legacy-paper-report --db-url <生产URL> "
        "复核 => 逐批 legacy-paper-migrate --yes（人工授权）=> 计数归零后"
        "导出 pending_count 与分批记录",
        "redaction": "只含 pending_count 与 batches[].executed 布尔；生产 "
        "paper ID 明细留在 artifacts 报告，不入证据文件",
        "semantics": "pending_count=0 => pass；pending_count>0 => pending"
        "（待人工决策/分批执行至归零）；无文件 => not_executed；结构不符/"
        "敏感键 => blocked",
        "authorization": _COMMON_AUTHORIZATION,
    },
    "draft-ownership": {
        "source": "python -m app.ops.cli draft-owner-report --db-url <生产URL> "
        "复核 => 逐批 draft-owner-migrate --yes（人工授权）=> 两类草稿计数"
        "归零后导出",
        "redaction": "只含 pending_count 与 batches[].executed 布尔；生产 "
        "draft ID 明细留在 artifacts 报告，不入证据文件",
        "semantics": "pending_count=0 => pass；pending_count>0 => pending；"
        "无文件 => not_executed；结构不符/敏感键 => blocked",
        "authorization": _COMMON_AUTHORIZATION,
    },
    "preflight-pre-migration": {
        "source": "切换窗口开始时 python -m app.ops.cli production-preflight "
        "--db-url <生产URL> --phase pre-migration（--output 可选，须在 "
        "artifacts/temp 内）",
        "redaction": "phase + summary 四计数；不含完整 DB URL/密码"
        "（preflight 输出自带脱敏，连接错误已抹 URL 内嵌凭据段）",
        "semantics": "summary.fail=0 => pass（pending/not_configured 属迁移前"
        "预期不阻断 pre 步；放行仍需 post-migration 证据）；fail>0 => blocked；"
        "无文件 => not_executed；四计数全 0/敏感键 => blocked",
        "authorization": _COMMON_AUTHORIZATION,
    },
    "backup-restore": {
        "source": "python -m app.ops.cli backup --db-url <生产URL> --out <备份"
        "目录>，并对隔离恢复库执行 restore 演练（verified）后导出 manifest 摘要",
        "redaction": "schema_version/created_at/restore_drill.verified/"
        "inserted_rows 摘要字段；不含备份内容、DB URL、S3 凭据",
        "semantics": "schema_version=aios-backup-v1 且 restore_drill.verified="
        "true => pass；manifest 在但 verified=false => pending（可恢复证据未"
        "闭合）；无文件 => not_executed；结构不符/敏感键 => blocked",
        "authorization": _COMMON_AUTHORIZATION,
    },
    "preflight-post-migration": {
        "source": "迁移完成后 python -m app.ops.cli production-preflight "
        "--db-url <生产URL> --phase post-migration --anchor-file <运维保管"
        "路径>/audit-anchor.jsonl",
        "redaction": "phase + summary 四计数；不含完整 DB URL/密码/锚文件"
        "敏感内容",
        "semantics": "无 fail 且无 pending/not_configured 且 pass>0 => pass"
        "（放行形态）；fail>0 => blocked；仍有 pending/not_configured => "
        "pending；无文件 => not_executed",
        "authorization": _COMMON_AUTHORIZATION,
    },
    "audit-chain-verify": {
        "source": "python -m app.ops.cli audit-chain-verify --db-url <生产URL>"
        "（valid 后导出；verify 先于锚定：链已断时锚定无意义）",
        "redaction": "valid/entries 两项；不含审计 before/after 正文与任何 "
        "DB URL",
        "semantics": "valid=true => pass；valid=false => blocked（按 runbook "
        "先修复链）；无文件 => not_executed；结构不符/敏感键 => blocked",
        "authorization": _COMMON_AUTHORIZATION,
    },
    "audit-chain-anchor": {
        "source": "人工执行 python -m app.ops.cli audit-chain-anchor --db-url "
        "<生产URL> --anchor-file <路径> --yes 追加锚点，归档 WORM/对象锁/离线"
        "介质后导出；可选：将锚文件副本复制为证据目录内 audit-anchor.jsonl"
        "（提供时 rehearsal 独立校验其自洽性）",
        "redaction": "anchor.status/anchors 与 worm.archived 三项白名单标量；"
        "锚文件副本是锚行 JSONL（只有 schema/algorithm/sequence/hash/时间，"
        "无敏感值）；不含 DB URL",
        "semantics": "anchor.status=up-to-date 且 worm.archived=true => pass"
        "（提供副本时另需副本校验自洽且锚点数一致）；落后（非 up-to-date）或"
        "未归档 => pending；invalid/副本校验失败/申报锚点数与副本不一致 => "
        "blocked；无文件 => not_executed",
        "authorization": _COMMON_AUTHORIZATION,
    },
    "cutover-approval": {
        "source": "人工填写：其余 12 步全 pass 后，由审批人逐项核对全部证据，"
        "计算并绑定各步证据文件与 supporting 文件的 sha256（可用 approval-draft "
        "底稿辅助，但必须人工逐项确认），填写窗口/回滚/观察期/说明/审批人",
        "redaction": "只含结构字段与哈希；note 只验证非空且 rehearsal 不回显；"
        "不写任何鉴权材料、完整 URL 凭据、生产业务 ID",
        "semantics": "step_evidence 覆盖其余 12 步且哈希与当前文件全部匹配、"
        "supporting_evidence 精确覆盖当前实际 supporting 文件 => pass；缺审批/"
        "覆盖缺口 => not_executed；哈希失配/引用不存在的 supporting 文件 => "
        "blocked；approved_by 只做记录不做身份认证",
        "authorization": "审批是人工决策动作：审批人必须逐项确认每份证据内容"
        "与哈希后才可签署；本工具与 rehearsal 均不代为审批",
    },
}

#: 隔离 fixture 形态（构造演练用 pass 形态证据的最小示例；仅供 temp/ 演练
#: 目录复现 ready，不代表生产验收——生产证据必须来自真实执行）。
FIXTURE_FORMS: dict[str, str] = {
    "ci-main": '{"step":"ci-main","run_id":1,"merge_commit":"aa11bb22cc33",'
    '"conclusion":"success"}',
    "release-check": '{"step":"release-check","all_green":true,"total":1,'
    '"passed":1,"failed_ids":[]}',
    "search-smoke": '{"step":"search-smoke","executed":true,"result":"pass"}',
    "cloud-voice-smoke": '{"step":"cloud-voice-smoke","executed":true,'
    '"result":"pass"}',
    "llm-smoke": '{"step":"llm-smoke","executed":true,"result":"pass"}',
    "legacy-papers": '{"step":"legacy-papers","pending_count":0,'
    '"batches":[{"executed":true}]}',
    "draft-ownership": '{"step":"draft-ownership","pending_count":0,'
    '"batches":[{"executed":true}]}',
    "preflight-pre-migration": '{"step":"preflight-pre-migration",'
    '"phase":"pre-migration","summary":{"pass":3,"pending":2,"fail":0,'
    '"not_configured":1}}',
    "backup-restore": '{"step":"backup-restore","schema_version":'
    '"aios-backup-v1","created_at":"2026-01-01T00:00:00+00:00",'
    '"restore_drill":{"verified":true,"inserted_rows":1}}',
    "preflight-post-migration": '{"step":"preflight-post-migration",'
    '"phase":"post-migration","summary":{"pass":6,"pending":0,"fail":0,'
    '"not_configured":0}}',
    "audit-chain-verify": '{"step":"audit-chain-verify","valid":true,'
    '"entries":1}',
    "audit-chain-anchor": '{"step":"audit-chain-anchor","anchor":{"status":'
    '"up-to-date","anchors":1},"worm":{"archived":true}}（演练最简形态：不'
    '提供 audit-anchor.jsonl 副本，supporting_evidence 用空对象；若要演练副本'
    '绑定，用 audit-chain-anchor 工具的 build_anchor 造自洽锚链）',
    "cutover-approval": "其余 12 步就位后：python -m app.ops.cli "
    "cutover-evidence-pack approval-draft --evidence-dir . 取得哈希底稿，"
    "人工填写 schema_version=1 / approved_at / note / window.start / "
    "window.end / rollback_plan / observation / approved_by，并把 "
    "step_evidence 与 supporting_evidence 换成底稿哈希（人工逐项确认后）",
}


# --- README 手册渲染 -------------------------------------------------------------


def render_pack_readme() -> str:
    """脚手架 README（操作手册）——确定性纯文本，无时间戳（幂等重放可比对）。"""
    lines: list[str] = [
        "# 生产切换 evidence pack 脚手架与操作手册（cutover evidence pack）",
        "",
        "> **DRAFT/REPLACE-ME**：本目录是脚手架——模板不是证据，本手册不是",
        "> 生产验收，也不授权任何生产写入/发布。真实生产操作必须运维/审批人",
        "> **逐项显式授权后人工执行**。",
        "",
        "配套工具：python -m app.ops.cli cutover-rehearsal --evidence-dir <dir>",
        "（M10-15 只读编排器，判定 13 步证据与审批哈希绑定）。",
        "",
        "## 快速上手（隔离演练三步）",
        "",
        "1. 把本目录复制到 temp/ 下的**演练目录**（不要直接在脚手架里填证据），",
        "   把每个 *.template.json 填入真实值并改名为对应证据文件名",
        "   （_target_file 字段标明目标名）；",
        "2. python -m app.ops.cli cutover-rehearsal --evidence-dir <演练目录>；",
        "3. rehearsal_ready=true 仅说明演练时间线 13 步证据齐备且审批绑定",
        "   完整——**不代表生产验收**，生产放行仍须按各 runbook 人工执行。",
        "",
        "## 安全边界（先读）",
        "",
        "- 脚手架生成器与 rehearsal 都不连接数据库/网络/API、不读取环境变量与",
        "  密钥、不执行迁移/锚定/清理/备份/部署/发布/回滚，无 --yes 执行形态；",
        "- 模板防误用：.template.json 命名不会被 rehearsal 当作证据（只会列入",
        "  unrecognized_files）；预填值全部 REPLACE-ME（类型/枚举故意不符），",
        "  即使改名直用也只能得到 blocked；",
        "- 证据文件不得携带敏感键（password/secret/token/api_key 等变体）或",
        "  URL 内嵌凭据段（user:pass@ 形态）——命中即 blocked，值从不回显；",
        "- 生产 URL/凭据永远不进 git、不进命令文档示例、不回显日志（手册中以",
        "  <生产URL> 占位）。",
        "",
        "## 13 个 required steps 逐项说明",
        "",
    ]
    for stage in STAGES:
        lines.append("### 阶段：" + stage + "｜" + STAGE_TITLES[stage])
        lines.append("")
        for spec in STEPS:
            if spec.stage != stage:
                continue
            entry = MANUAL_ENTRIES[spec.step_id]
            lines.append("#### " + spec.step_id + "——" + spec.title)
            lines.append("")
            lines.append(
                "- 证据文件：" + spec.evidence_file
                + "（模板：" + template_name(spec.evidence_file) + "）"
            )
            lines.append("- 为什么必需：" + spec.basis)
            lines.append("- 来源命令/runbook：" + entry["source"])
            lines.append("- 脱敏要求：" + entry["redaction"])
            lines.append("- 通过/失败语义：" + entry["semantics"])
            lines.append("- 人工授权：" + entry["authorization"])
            lines.append("- 隔离 fixture 形态：" + FIXTURE_FORMS[spec.step_id])
            lines.append("")
    lines += [
        "## 审批与 SHA-256 计算（cutover-approval.json）",
        "",
        "- step_evidence：其余 12 步每份证据**文件字节**的 SHA-256（64 位",
        "  小写十六进制）；supporting_evidence：目录内实际存在的 supporting",
        "  文件（目前仅 audit-anchor.jsonl）的 sha256 mapping，目录没有副本",
        "  时必须为空对象；",
        "- 辅助计算（仍是 DRAFT，须人工逐项确认）：",
        "  python -m app.ops.cli cutover-evidence-pack approval-draft",
        "  --evidence-dir <演练目录>；",
        "- 手工计算（任一平台等价）：",
        (
            '  python -c "import hashlib,sys;print(hashlib.sha256('
            "open(sys.argv[1],'rb').read()).hexdigest())\""
        ),
        "  <证据文件>；",
        "- 审批后任何证据/supporting 文件被改动，rehearsal 都会判 blocked",
        "  （哈希绑定失配）——改动后须重新导出并重新审批。",
        "",
        "## 隔离 fixture 全链路（temp/ 内复现 rehearsal_ready）",
        "",
        "1. python -m app.ops.cli cutover-evidence-pack scaffold",
        "   --target-dir temp/cutover-pack-dryrun；",
        "2. 复制到演练目录（如 temp/cutover-fixture/），按上方各步",
        "  「隔离 fixture 形态」填写并改名为真实证据文件名（共 12 步 + 审批）；",
        "3. python -m app.ops.cli cutover-rehearsal --evidence-dir",
        "   temp/cutover-fixture => 13 步全 pass、rehearsal_ready=true；",
        "4. **声明**：以上只是隔离 fixture 演练——fixture 值不代表任何真实",
        "  执行结果，ready 不代表生产验收，也不授权生产写入/发布；生产证据",
        "  必须来自人工逐项授权后的真实执行与导出。",
        "",
        "（本 README 由 app/ops/cutover_evidence_pack.py 渲染生成，内容与仓库",
        " docs/DEVELOPMENT.md「生产切换证据包脚手架（M10-16）」节同源。）",
    ]
    return "\n".join(lines) + "\n"


def build_pack_files() -> dict[str, bytes]:
    """脚手架全部产物（确定性字节：无时间戳，幂等重放可逐字节比对）。"""
    files: dict[str, bytes] = {}
    for spec in STEPS:
        payload = build_step_template(spec.step_id)
        files[template_name(spec.evidence_file)] = (
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
    files[template_name(ANCHOR_COMPANION_FILE)] = build_anchor_companion_template()
    files[README_FILE] = render_pack_readme().encode("utf-8")
    return files

# --- scaffold：目标目录护栏 + 写入 ------------------------------------------------


def _reject_symlink_ancestry(path: Path) -> None:
    """路径上任何**已存在**组件是符号链接即拒绝（fail-closed，不猜目标）。"""
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise PackInputError("路径组件是符号链接，拒绝写入: " + str(candidate))


def resolve_scaffold_target(raw: str | Path) -> Path:
    """scaffold 目标目录护栏：artifacts/temp gitignore 边界内的新目录。

    - 任何已存在路径组件是 symlink => 拒绝（含 .. 越界经 resolve 归一后
      仍须落在护栏内）；
    - 复用 legacy 报告的 is_safe_artifact_path（直接父目录名是 artifacts/
      temp，或 git check-ignore 判定被忽略——仓库外路径 git 无法判定即拒绝）；
    - 目标本身就是 artifacts/ 或 temp/ 目录 => 拒绝（要求脚手架独占新目录）。
    """
    target = Path(raw)
    _reject_symlink_ancestry(target)
    resolved = target.resolve()
    if resolved.name.lower() in ("artifacts", "temp"):
        raise PackInputError(
            "目标目录不能是 artifacts/ 或 temp/ 本身（必须是其中独占的新目录）: "
            + str(raw)
        )
    if not is_safe_artifact_path(resolved):
        raise PackInputError(
            "拒绝写入 " + str(raw) + "：脚手架只能创建在 gitignore 的 "
            "artifacts/ 或 temp/ 目录内（直接父目录名为 artifacts/temp，或被 "
            "git check-ignore 判定忽略；仓库外任意路径一律拒绝）"
        )
    return resolved


def _assert_identical_scaffold(
    target: Path, existing: dict[str, Path], files: dict[str, bytes]
) -> None:
    """已存在目录的幂等判定：文件名集合与每个文件字节都和脚手架完全一致；
    任何多余/缺失/被改/非常规文件条目都拒绝（绝不覆盖既有内容）。"""
    expected = set(files)
    actual = set(existing)
    if actual != expected:
        raise PackInputError(
            "目标目录非空且不是本工具的完整脚手架（拒绝覆盖）: " + str(target)
            + "（多余: " + repr(sorted(actual - expected))
            + "；缺失: " + repr(sorted(expected - actual)) + "）"
        )
    for name in sorted(existing):
        path = existing[name]
        if path.is_symlink() or not path.is_file():
            raise PackInputError("脚手架条目不是常规文件（拒绝覆盖）: " + name)
        if path.read_bytes() != files[name]:
            raise PackInputError(
                name + " 内容与本工具脚手架不一致（疑似已填写，拒绝覆盖）"
            )


def scaffold_pack(target_dir: str | Path) -> dict[str, Any]:
    """在护栏内新目录生成脚手架；目录已是同一脚手架时幂等（零改写）。"""
    files = build_pack_files()
    target = resolve_scaffold_target(target_dir)
    report: dict[str, Any] = {
        "tool": "cutover-evidence-pack",
        "action": "scaffold",
        "target_dir": str(target),
        "idempotent": False,
        "files_written": [],
        "files_present": [],
    }
    if os.path.lexists(target):
        if target.is_symlink() or not target.is_dir():
            raise PackInputError("目标已存在且不是目录: " + str(target))
        existing = {entry.name: entry for entry in sorted(target.iterdir())}
        if existing:
            _assert_identical_scaffold(target, existing, files)
            report["idempotent"] = True
            report["files_present"] = sorted(existing)
            return report
    target.mkdir(parents=True, exist_ok=True)
    for name in sorted(files):
        path = target / name
        if os.path.lexists(path):
            # 竞态兜底：存在即拒绝覆盖（前置目录级判定之外的单文件复核）
            raise PackInputError("目标内已存在文件，拒绝覆盖: " + name)
        path.write_bytes(files[name])
        report["files_written"].append(name)
    return report


# --- approval-draft：本地哈希底稿（DRAFT，非审批记录） ---------------------------

#: 审批人必须人工填写的字段（approval-draft 只算哈希，不代填、不代审批）
MANUAL_APPROVAL_FIELDS: dict[str, str] = {
    "schema_version": "整数：固定 1",
    "approved_at": "REPLACE-ME：审批时间（ISO 8601，人工填写）",
    "note": "REPLACE-ME：审批说明（人工填写；rehearsal 只验证非空，不回显）",
    "window.start": "REPLACE-ME：切换窗口开始（ISO 8601，须早于 end）",
    "window.end": "REPLACE-ME：切换窗口结束（ISO 8601）",
    "rollback_plan": "REPLACE-ME：回滚计划（人工填写）",
    "observation": "REPLACE-ME：观察期安排（人工填写）",
    "approved_by": "REPLACE-ME：审批人记录（只做记录，不做身份认证）",
}


def build_approval_draft(evidence_dir: str | Path) -> dict[str, Any]:
    """只读计算当前证据目录的审批哈希底稿（DRAFT；缺 step/必填审批字段，
    直接改名只会 blocked）。哈希范围=审批应绑定的其余 12 步主证据 + 当前
    实际存在的 supporting 文件（cutover-approval.json 自身不绑定自己，已
    存在时只在 approval_file_present 如实标注）。装载层问题（非法 JSON/
    敏感键/内嵌凭据）如实摘要列出（只报字段路径，值不回显），不阻断底稿
    生成——最终判定仍以 cutover-rehearsal 为准。"""
    from datetime import UTC, datetime

    root = check_evidence_dir(Path(evidence_dir))
    approval_present = False
    step_hashes: dict[str, str] = {}
    missing: list[str] = []
    problems: dict[str, str] = {}
    for spec in STEPS:
        path = root / spec.evidence_file
        if spec.step_id == "cutover-approval":
            approval_present = os.path.lexists(path)
            continue  # 审批只绑定其余 12 步，不绑定自身
        if not os.path.lexists(path):
            missing.append(spec.step_id)
            continue
        check_regular_file(path)
        step_hashes[spec.step_id] = sha256_file(path)
        obj, problem = load_json_object(path)
        if problem is None:
            hit = find_sensitive_key(obj)
            if hit is not None:
                problem = "证据含敏感键 " + hit + "（rehearsal 将判 blocked；值不回显）"
        if problem is None:
            embedded = find_embedded_credential(obj)
            if embedded is not None:
                problem = (
                    "证据字段 " + embedded + " 内嵌凭据（rehearsal 将判 blocked；"
                    "值不回显）"
                )
        if problem is not None:
            problems[spec.evidence_file] = problem
    supporting: dict[str, str] = {}
    for name in sorted(BINDABLE_SUPPORTING_FILES):
        path = root / name
        if os.path.lexists(path):
            check_regular_file(path)
            supporting[name] = sha256_file(path)
    draft = {
        "draft": True,
        "tool": "cutover-evidence-pack approval-draft",
        "generated_at": datetime.now(UTC).isoformat(),
        "evidence_dir": str(root),
        "notice": APPROVAL_DRAFT_NOTICE,
        "approval_file_present": approval_present,
        "step_evidence": step_hashes,
        "step_evidence_missing": missing,
        "supporting_evidence": supporting,
        "load_problems": problems,
        "manual_fields_required": dict(MANUAL_APPROVAL_FIELDS),
        "confirmation_required": (
            "审批人必须逐项人工确认每份证据内容并核对哈希后，自行组装 "
            "cutover-approval.json（本底稿不是审批记录，直接改名只会 blocked）；"
            "任何真实生产操作仍须人工逐项授权执行"
        ),
    }
    return scrub_sensitive(draft)
