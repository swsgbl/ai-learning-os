"""M11-16 provider-smoke-evidence：provider 冒烟证据导出与聚合。

定位：cutover-rehearsal（M10-15）的 ``search-smoke`` / ``cloud-voice-smoke`` /
``llm-smoke`` 三步与 release-readiness（M10-11）的 ``provider-smoke`` 门此前的
证据只能人工抄录冒烟结论拼装（M10-16 手册「人工抄录脱敏结果」口径——转抄
没有任何交叉校验，抄错即证据失真）。本工具把既有三个冒烟脚本的真实运维
执行结果自动导出为脱敏、原子、机器可读证据（与 backup-restore-evidence
（M11-04）/ governance-evidence（M11-12）同一动机：人工拼装改为机器可复现
导出）：

- 单 provider 执行导出（CLI ``provider-smoke-export``）：以 bash 运行既有
  脚本（``infra/smoke_search.sh`` / ``infra/smoke_voice_cloud.sh`` /
  ``infra/smoke_llm.sh``，cwd=仓库根 + 相对 POSIX 路径——Windows 绝对路径
  在 WSL bash 下不可解析），子进程整体继承当前环境与终端（脚本自身的脱敏
  摘要直通运维终端，不捕获不保存）；runner 退出码 0 => ``result=pass``
  （CLI exit 0），非零 => ``result=fail``（失败证据照常原子落盘，CLI
  exit 1——如实记录，不伪装 pass）；
- 三份单步证据聚合（CLI ``provider-smoke-aggregate``）：确定性拼装
  ``provider-smoke.json``（``providers.voice/search/llm`` 每项仅 ``executed``
  与 ``result``——release-readiness 既有 evaluator 直接消费；不透传单步
  exit_code/时间/脚本细节）；任一 fail => 聚合证据照常落盘、CLI exit 1。

安全护栏（全部先于 runner 执行；违例 exit 2、不运行冒烟、不写证据、不创建
输出/父目录）：

- provider 名必须是合法枚举；bash（PATH 查找）或既有脚本文件不可用即拒绝
  （编排环境问题不是冒烟结论）；runner 抛 OSError 同样 exit 2、不写证据；
- 输出必须位于 gitignore 的 artifacts/temp（复用 ``is_safe_artifact_path``）；
  任何已存在路径组件（含自身）是 symlink 即拒绝；已存在且不是常规文件
  （目录等）拒绝；文件名必须恰为对应步/门的精确证据文件名（manifest 工具
  只认精确文件名，防笔误产出不可消费文件）；
- 聚合的三份输入必须都位于 artifacts/temp 且为常规文件（symlink 拒绝），
  且**必须确为本工具导出的单步证据**（exact schema）：顶层键集合恰为
  ``tool``/``schema_version``/``step``/``executed``/``result``/``exit_code``/
  ``started_at``/``completed_at``/``duration_ms`` 九键（缺字段/多字段均拒绝
  ——缺 metadata 或携带额外字段的 JSON 无法确为本工具导出）；
  ``tool`` / ``schema_version`` 精确匹配、``step`` 与 provider 槽位精确匹配、
  ``executed=true``、``result`` 只能 pass/fail 且与 ``exit_code`` 结论一致
  （pass => 0、fail => 非 0）、``exit_code``/``duration_ms`` 为非 bool int
  （后者非负）、起止时间为 timezone-aware ISO 字符串且 ``completed_at >=
  started_at``——手工拼装、槽位错位、not_executed 形态、metadata 漂移一律
  fail-closed 拒绝；聚合输出不得等于任何输入、同一输入文件不得重复传入两个
  槽位（``resolve`` + ``os.path.normcase`` 归一比较——Windows 大小写与路径
  分隔符形态差异不构成绕过）；
- 父目录创建与原子写发生在 runner/校验之后（复用 CLI ``_write_report_atomic``
  语义：同目录临时文件 + fsync + os.replace；失败旧文件字节原样、无 .tmp
  残留）。

脱敏边界：本模块不读取任何敏感环境变量（provider 端点/密钥/模型名/查询词/
音频路径等槽位一概不检查——冒烟所需的 provider 端点/凭据只由运维在调用前
注入环境）；对环境的访问仅限 ``shutil.which`` 经 PATH 解析 bash 路径
（编排检查）与子进程对当前环境的整体继承。不捕获不保存子进程
stdout/stderr；证据只含白名单标量（step/gate、executed、result、
exit_code、起止时间与耗时）——无命令行、无 endpoint、无模型名、无查询词、
无音频路径、无转写正文、无任何摘要文本。

隔离声明：本工具只编排「运维已决定执行的冒烟」并导出结论证据——真实
冒烟所需的 key/端点由运维显式提供，本工具不自动补跑任何冒烟、不改变三个
冒烟脚本的判定逻辑、不放宽 provider-smoke gate 语义；模块自身不连接数据
库、不访问网络（真实端点调用只发生在冒烟脚本内部）、不执行任何生产操作。

退出码：结论 pass=0 / 结论 fail=1（证据照常落盘）/ 输入、路径、编排或写入
失败=2。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.ops.legacy_papers import is_safe_artifact_path

#: 证据自声明：聚合输入校验用（同 governance-evidence 的 TOOL_ID 模式）
TOOL_ID = "provider-smoke-evidence"
SCHEMA_VERSION = "provider-smoke-evidence-v1"

#: 聚合证据自声明的门（release-readiness 的 provider-smoke 门）与其精确文件名
GATE_ID = "provider-smoke"
GATE_OUTPUT_FILE = "provider-smoke.json"

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]

_REDACTION_NOTE = (
    "证据只含结论白名单标量：无命令行/stdout/stderr/endpoint/模型名/查询词/"
    "音频路径/转写正文；脚本脱敏摘要直通运维终端，不进入证据"
)
_NO_EXECUTION_NOTE = (
    "只编排运维显式执行的冒烟并导出结论证据：不自动补跑任何冒烟、不改变"
    "冒烟脚本判定逻辑、不读取任何敏感环境变量（唯一环境访问是 PATH 上解析"
    "bash，子进程整体继承环境）、不捕获脚本输出；模块自身不连接数据库、"
    "不访问网络、不执行任何生产操作"
)


class ProviderSmokeInputError(Exception):
    """参数/路径/聚合输入校验问题（CLI exit 2：不运行冒烟、不写证据）。"""


class ProviderSmokeExecutionError(Exception):
    """编排环境问题：bash/脚本不可用或 runner 无法启动（CLI exit 2，不写证据）。"""


@dataclass(frozen=True)
class ProviderSpec:
    provider: str  # CLI provider 名
    script: str  # 仓库内相对 POSIX 路径（配合 cwd=仓库根调用）
    step: str  # cutover-rehearsal 的精确 step id
    gate_key: str  # 聚合 providers 键（对齐 release-readiness SMOKE_PROVIDERS）


#: provider -> 既有冒烟脚本与证据契约映射（测试锁定其稳定性）
PROVIDERS: dict[str, ProviderSpec] = {
    "search": ProviderSpec(
        "search", "infra/smoke_search.sh", "search-smoke", "search"
    ),
    "cloud-voice": ProviderSpec(
        "cloud-voice", "infra/smoke_voice_cloud.sh", "cloud-voice-smoke", "voice"
    ),
    "llm": ProviderSpec("llm", "infra/smoke_llm.sh", "llm-smoke", "llm"),
}

#: step -> rehearsal 精确证据文件名（与 cutover_rehearsal.STEPS 同步）
STEP_OUTPUT_FILES: dict[str, str] = {
    "search-smoke": "search-smoke.json",
    "cloud-voice-smoke": "cloud-voice-smoke.json",
    "llm-smoke": "llm-smoke.json",
}

#: 单步证据可携带的 result 枚举（本工具只产出 pass/fail；not_executed 是
#: 人工「未跑」声明形态，机器导出不产生，聚合时 fail-closed 拒绝）
RESULT_VALUES = ("pass", "fail")

#: 单步证据的完整精确键集（exact schema）：聚合输入顶层键集合必须与之
#: 完全相等——缺任一字段或携带额外字段都不是本工具导出的单步证据形态。
#: 与 build_step_evidence 的证据键一一对应（测试交叉锁定）。
STEP_EVIDENCE_KEYS = (
    "tool",
    "schema_version",
    "step",
    "executed",
    "result",
    "exit_code",
    "started_at",
    "completed_at",
    "duration_ms",
)

#: 聚合输出 providers 的键序（release-readiness SMOKE_PROVIDERS 同序）
GATE_PROVIDER_KEYS = ("voice", "search", "llm")


def _utc_now() -> datetime:
    return datetime.now(UTC)


# --- 路径护栏 -------------------------------------------------------------------


def _reject_symlink_components(path: Path, label: str) -> None:
    """路径任何已存在组件（含自身）是 symlink 即拒绝（fail-closed）。

    先于 is_safe_artifact_path 执行：后者内部 resolve() 会跟随 symlink，
    链接目标落在护栏内时会被误放行（同 governance-evidence 口径）。
    """
    chain: list[Path] = []
    current = path
    while current.name:
        chain.append(current)
        current = current.parent
    for item in reversed(chain):
        if item.is_symlink():
            raise ProviderSmokeInputError(
                f"{label}路径组件是符号链接，拒绝使用: {item}"
            )


def _check_input_file(path: str | Path, label: str) -> Path:
    """聚合输入护栏：artifacts/temp 内、非 symlink、已存在的常规文件。"""
    target = Path(path)
    _reject_symlink_components(target, label)
    if not is_safe_artifact_path(target):
        raise ProviderSmokeInputError(
            f"{label}必须位于 gitignore 的 artifacts/ 或 temp/ 目录: {target}"
        )
    if not target.exists():
        raise ProviderSmokeInputError(f"{label}不存在: {target}")
    if not target.is_file():
        raise ProviderSmokeInputError(f"{label}不是常规文件: {target}")
    return target


def _check_output_path(path: str | Path, expected_name: str) -> Path:
    """输出护栏：artifacts/temp 内、非 symlink、文件名恰为精确证据文件名、
    已存在时必须是常规文件。只校验不创建——父目录由 CLI 在 runner 之后创建。"""
    target = Path(path)
    _reject_symlink_components(target, "输出")
    if not is_safe_artifact_path(target):
        raise ProviderSmokeInputError(
            f"输出必须位于 gitignore 的 artifacts/ 或 temp/ 目录: {target}"
        )
    if target.name != expected_name:
        raise ProviderSmokeInputError(
            f"输出文件名必须是 {expected_name}（manifest 工具只认精确证据"
            f"文件名，防笔误产出不可消费文件）: {target.name}"
        )
    if target.exists() and not target.is_file():
        raise ProviderSmokeInputError(f"输出路径已存在且不是常规文件: {target}")
    return target


def _path_key(path: Path) -> str:
    """输入/输出冲突比较键：``resolve`` 后 ``normcase`` 归一（Windows 上
    大小写与路径分隔符形态差异归为同一键，书写形态不构成绕过）。symlink
    已在前置护栏拒绝，``resolve`` 不会跟随到意外目标。"""
    return os.path.normcase(str(path.resolve()))


def _reject_output_overlapping_input(
    output: Path, inputs: Mapping[str, Path]
) -> None:
    """输出不得等于任何输入；同一输入文件不得重复传入两个槽位（重复会
    用一份证据冒充两个 provider 的覆盖面）。"""
    output_key = _path_key(output)
    seen: dict[str, str] = {}
    for key, path in inputs.items():
        item_key = _path_key(path)
        if item_key == output_key:
            raise ProviderSmokeInputError(
                f"输出路径与{key}单步证据是同一文件（拒绝覆盖输入）: {output}"
            )
        if item_key in seen:
            raise ProviderSmokeInputError(
                f"{key}与{seen[item_key]}槽位传入了同一份单步证据（重复输入"
                "不得虚增 provider 覆盖面）"
            )
        seen[item_key] = key


# --- 装载与校验 -----------------------------------------------------------------


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise ProviderSmokeInputError(
            f"{label}不是有效 UTF-8: {path.name}"
        ) from None
    except OSError as cause:
        raise ProviderSmokeInputError(f"{label}无法读取: {cause}") from None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as cause:
        raise ProviderSmokeInputError(
            f"{label}不是合法 JSON: {cause.msg}（第 {cause.lineno} 行）"
        ) from None
    if not isinstance(obj, dict):
        raise ProviderSmokeInputError(f"{label}顶层不是 JSON 对象: {path.name}")
    return obj


def _require_aware_timestamp(value: Any, field: str, label: str) -> datetime:
    """时间字段必须是可 ``datetime.fromisoformat`` 解析的 timezone-aware
    ISO 字符串（本工具导出的是 aware ``isoformat()`` 文本——naive 或无法
    解析的值都是手工拼装形态，fail-closed 拒绝）。"""
    if not isinstance(value, str):
        raise ProviderSmokeInputError(
            f"{label} {field} 必须是 ISO 8601 字符串，实际类型: "
            f"{type(value).__name__}"
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ProviderSmokeInputError(
            f"{label} {field} 不是可解析的 ISO 8601 时间戳: {value!r}"
        ) from None
    if parsed.tzinfo is None:
        raise ProviderSmokeInputError(
            f"{label} {field} 缺少时区信息（naive 时间不是本工具导出形态）: "
            f"{value!r}"
        )
    return parsed


def _require_step_result(obj: Mapping[str, Any], spec: ProviderSpec) -> str:
    """单步证据必须确为本工具导出的**完整精确形态**（exact schema）：
    顶层键集合恰为 :data:`STEP_EVIDENCE_KEYS` 九键（缺字段/多字段均拒绝——
    缺 metadata 或携带额外字段的 JSON 无法确为本工具导出）；``tool`` /
    ``schema_version`` 精确匹配、``step`` 与 provider 槽位精确匹配、
    ``executed=true``、``result`` 只能 pass/fail；``exit_code`` 为非 bool
    int 且与 result 结论一致（pass => 0、fail => 非 0）；``started_at`` /
    ``completed_at`` 为 timezone-aware ISO 字符串且 ``completed_at >=
    started_at``；``duration_ms`` 为非 bool 非负 int——手工拼装、槽位错位、
    未执行形态、metadata 漂移一律 fail-closed 拒绝。"""
    label = f"{spec.provider} 单步证据"
    if set(obj) != set(STEP_EVIDENCE_KEYS):
        missing = sorted(set(STEP_EVIDENCE_KEYS) - set(obj))
        extra = sorted(set(obj) - set(STEP_EVIDENCE_KEYS))
        raise ProviderSmokeInputError(
            f"{label}顶层键集合必须恰为 {list(STEP_EVIDENCE_KEYS)}"
            f"（缺失: {missing}，多余: {extra}——缺字段/多字段都不是本工具"
            "导出的单步证据形态）"
        )
    if obj["tool"] != TOOL_ID:
        raise ProviderSmokeInputError(
            f"{label}不是 {TOOL_ID} 导出的证据（tool 不匹配——手工拼装或"
            "来源不明的证据不得聚合）"
        )
    if obj["schema_version"] != SCHEMA_VERSION:
        raise ProviderSmokeInputError(f"{label} schema_version 与本工具不一致")
    if obj["step"] != spec.step:
        raise ProviderSmokeInputError(
            f"{label}的 step 应为 {spec.step}（槽位错位——provider 与证据不对应）"
        )
    if obj["executed"] is not True:
        raise ProviderSmokeInputError(
            f"{label} executed 非 true（未执行形态的证据不得聚合）"
        )
    result = obj["result"]
    if result not in RESULT_VALUES:
        raise ProviderSmokeInputError(
            f"{label} result 只能是 {list(RESULT_VALUES)}（本工具导出不产生"
            "其它形态）"
        )
    exit_code = obj["exit_code"]
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise ProviderSmokeInputError(
            f"{label} exit_code 必须是 int（bool 不是合法退出码形态）: "
            f"{exit_code!r}"
        )
    if result == "pass" and exit_code != 0:
        raise ProviderSmokeInputError(
            f"{label} result=pass 但 exit_code={exit_code}（结论与退出码矛盾，"
            "不是本工具导出形态）"
        )
    if result == "fail" and exit_code == 0:
        raise ProviderSmokeInputError(
            f"{label} result=fail 但 exit_code=0（结论与退出码矛盾，不是本工具"
            "导出形态）"
        )
    started = _require_aware_timestamp(obj["started_at"], "started_at", label)
    completed = _require_aware_timestamp(
        obj["completed_at"], "completed_at", label
    )
    if completed < started:
        raise ProviderSmokeInputError(
            f"{label} completed_at 早于 started_at（时间倒置不是本工具导出"
            f"形态）: {obj['completed_at']!r} < {obj['started_at']!r}"
        )
    duration_ms = obj["duration_ms"]
    if (
        isinstance(duration_ms, bool)
        or not isinstance(duration_ms, int)
        or duration_ms < 0
    ):
        raise ProviderSmokeInputError(
            f"{label} duration_ms 必须是非负 int（bool 不算）: {duration_ms!r}"
        )
    return result


# --- 执行与组装 -----------------------------------------------------------------


def _run_smoke_script(argv: list[str]) -> subprocess.CompletedProcess:
    """真实 runner：bash 脚本 cwd=仓库根、整体继承当前环境（不构造 env=、
    不捕获输出——脚本自身的脱敏摘要直通运维终端）。``check=False``：退出码
    是冒烟结论，不是异常。"""
    return subprocess.run(argv, cwd=str(_REPOSITORY_ROOT), check=False)


def build_step_evidence(
    provider: str,
    output_path: str | Path,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess] | None = None,
    bash: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> tuple[dict[str, Any], int]:
    """运行单 provider 冒烟并组装单步证据（不写文件；落盘由 CLI 原子完成）。

    返回 (evidence, exit_code)：runner 退出码 0 -> (pass, 0)；非零 ->
    (fail, 1)（证据照常返回，由 CLI 原子落盘——如实记录，不伪装 pass）。
    护栏/编排违例抛 :class:`ProviderSmokeInputError`（路径/参数）或
    :class:`ProviderSmokeExecutionError`（bash/脚本不可用、runner OSError）
    ——CLI exit 2、不写证据。``runner``/``bash``/``clock`` 可注入，测试零
    子进程、零网络。
    """
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise ProviderSmokeInputError(
            f"未知 provider: {provider!r}（合法值: {sorted(PROVIDERS)}）"
        )
    _check_output_path(output_path, STEP_OUTPUT_FILES[spec.step])
    bash_path = bash or shutil.which("bash")
    if not bash_path:
        raise ProviderSmokeExecutionError(
            "PATH 上找不到 bash（冒烟脚本需要 bash 执行——编排环境问题，"
            "不是冒烟结论）"
        )
    script_file = _REPOSITORY_ROOT / spec.script
    if not script_file.is_file():
        raise ProviderSmokeExecutionError(
            f"冒烟脚本不存在或不是常规文件: {spec.script}"
        )
    now = clock or _utc_now
    started_at = now()
    try:
        proc = (runner or _run_smoke_script)([bash_path, spec.script])
    except OSError as cause:
        raise ProviderSmokeExecutionError(
            f"冒烟脚本无法启动（未产生证据）: {type(cause).__name__}: {cause}"
        ) from None
    completed_at = now()
    duration_ms = max(0, int((completed_at - started_at).total_seconds() * 1000))
    result = "pass" if proc.returncode == 0 else "fail"
    evidence: dict[str, Any] = {
        "tool": TOOL_ID,
        "schema_version": SCHEMA_VERSION,
        "step": spec.step,
        "executed": True,
        "result": result,
        "exit_code": proc.returncode,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_ms": duration_ms,
    }
    return evidence, 0 if result == "pass" else 1


def build_provider_smoke_evidence(
    step_evidence: Mapping[str, str | Path],
    output_path: str | Path,
    *,
    clock: Callable[[], datetime] | None = None,
) -> tuple[dict[str, Any], int]:
    """把三份本工具导出的单步证据聚合为 ``provider-smoke.json`` 证据
    （不写文件；落盘由 CLI 原子完成）。

    返回 (evidence, exit_code)：任一 provider fail -> (证据, 1)（照常返回，
    由 CLI 原子落盘——如实记录）；全 pass -> 0。输入校验违例抛
    :class:`ProviderSmokeInputError`（CLI exit 2、不写输出、输入字节不变）。
    """
    if set(step_evidence) != set(PROVIDERS):
        raise ProviderSmokeInputError(
            "聚合需要恰为三个 provider 槽位（--search/--cloud-voice/--llm）的"
            f"单步证据，实际槽位: {sorted(step_evidence)}"
        )
    inputs = {
        key: _check_input_file(step_evidence[key], f"{key} 单步证据")
        for key in sorted(PROVIDERS)
    }
    # 冲突护栏先于任何内容读取与写入：输出不得覆盖输入；同一输入不得重复。
    _reject_output_overlapping_input(
        _check_output_path(output_path, GATE_OUTPUT_FILE), inputs
    )
    results = {
        key: _require_step_result(
            _load_json_object(inputs[key], f"{key} 单步证据"), PROVIDERS[key]
        )
        for key in sorted(PROVIDERS)
    }
    # 聚合契约最小化：providers 每项仅 executed 与 result——单步 exit_code/
    # 起止时间/脚本细节一律不透传（release-readiness evaluator 不需要）。
    # 键序按 GATE_PROVIDER_KEYS（SMOKE_PROVIDERS 同序）组装，不随 CLI
    # provider 名的字母序漂移。
    provider_by_gate_key = {
        spec.gate_key: key for key, spec in PROVIDERS.items()
    }
    evidence: dict[str, Any] = {
        "tool": TOOL_ID,
        "schema_version": SCHEMA_VERSION,
        "gate": GATE_ID,
        "providers": {
            gate_key: {
                "executed": True,
                "result": results[provider_by_gate_key[gate_key]],
            }
            for gate_key in GATE_PROVIDER_KEYS
        },
        "generated_at": (clock or _utc_now)().isoformat(),
    }
    return evidence, 0 if all(v == "pass" for v in results.values()) else 1


# --- 人类可读摘要 -----------------------------------------------------------------


def format_step_summary(evidence: Mapping[str, Any]) -> str:
    """人类可读摘要（无凭据/无脚本输出/无敏感配置；只有结论与耗时）。"""
    step = evidence["step"]
    result = evidence["result"]
    lines = [
        "provider 冒烟证据导出（provider-smoke-export）",
        f"完成时间: {evidence['completed_at']}",
        (
            f"step: {step}  result={result}  exit_code={evidence['exit_code']}  "
            f"duration_ms={evidence['duration_ms']}"
        ),
    ]
    if result == "pass":
        lines.append(
            f"RESULT: PASS——{step} 冒烟通过，证据可直接作 cutover-rehearsal "
            "对应步的脱敏结果文件。"
        )
    else:
        lines.append(
            f"RESULT: FAIL——{step} 冒烟失败；失败证据已如实落盘（exit 1），"
            "排查后由运维重新执行冒烟并重新导出。"
        )
    lines.append(_REDACTION_NOTE)
    lines.append(_NO_EXECUTION_NOTE)
    lines.append(
        "退出码: pass=0 / fail=1（证据照常落盘）/ 输入或路径或编排或写入问题=2。"
    )
    return "\n".join(lines)


def format_aggregate_summary(evidence: Mapping[str, Any]) -> str:
    """人类可读摘要（无凭据/无敏感配置；只有三个 provider 的结论枚举）。"""
    providers = evidence["providers"]
    results = {name: providers[name]["result"] for name in GATE_PROVIDER_KEYS}
    lines = [
        "provider 冒烟聚合证据（provider-smoke-aggregate）",
        f"生成时间: {evidence['generated_at']}",
        "  ".join(f"{name}={result}" for name, result in results.items()),
    ]
    fails = [name for name, result in results.items() if result == "fail"]
    if fails:
        lines.append(
            f"RESULT: FAIL——{', '.join(fails)} 冒烟失败；聚合证据已如实落盘"
            "（exit 1），排查后重新执行对应冒烟并重新导出聚合。"
        )
    else:
        lines.append(
            "RESULT: PASS——voice/search/llm 三类 provider 冒烟全过，可作 "
            "release-readiness provider-smoke 门证据。"
        )
    lines.append(_NO_EXECUTION_NOTE)
    lines.append(
        "退出码: 全 pass=0 / 任一 fail=1（证据照常落盘）/ 输入或路径或写入问题=2。"
    )
    return "\n".join(lines)
