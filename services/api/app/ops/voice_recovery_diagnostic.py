"""M14-224 local 语音恢复诊断：M14-222 voice 阻塞的 fail-closed 机器可读恢复指令面。

定位：provider-smoke-preflight（M14-112）对 local 拓扑语音槽位只能给出
``not_ready``/``endpoint_absent`` + 泛化建议 ``start_externally_then_rerun``
——**不告诉运维该走哪条既有生命周期路径、前置是什么、恢复后按什么顺序
重跑**。M14-222 实证该缺口：ASR 8010 / TTS 8011 无监听 → local-voice
export 与三输入聚合全部诚实未执行，操作者只能人工翻文档拼恢复步骤。本
工具在不启动/停止/重启任何服务、不读任何 secret、不发真实语音请求的
前提下，把该阻塞转换为确定性恢复诊断报告：

- **listener 状态**（每引擎）：直接复用 preflight 的
  ``_voice_health_check``（同 ``/health`` 契约、同 10s 有界只读探测、
  同 loopback ``trust_env=False`` 纪律、同 status/reason/recommendation
  固定词汇闭集）——零第二套探测语义（测试交叉锁定输出与 preflight
  voice checks 全等）；
- **manifest 事实**（每引擎，只读）：读取既有生命周期管理器
  ``tools/voice/voice_service_control.py``（M14-04）的事实源
  ``artifacts/voice/<engine>/service/manifest.json``（在场/端口/记录端口
  匹配/PID/启动时间/bootstrap 脚本）——引擎规格（funasr=ASR 8010 /
  cosyvoice=TTS 8011、artifacts 子目录、bootstrap 脚本名）优先经
  importlib 从生命周期工具本体派生（与 ``production_recovery.py`` 的
  ``_load_voice_module`` 同款复用模式，不造第二份规格）；工具文件缺席
  （如容器 /app 打包布局，M14-211/M14-212 口径）时 fail-closed 降级为
  交叉锁定的 builtin 回退并如实报告 ``engine_specs_source``；
- **选定恢复路径 + 必需下一步动作**（闭集）：按 (listener, manifest)
  事实确定性映射——stopped（无监听无 manifest，M14-222/M14-145 实证
  形态）→ ``controlled_start``（先 status 权威核验再经受控工具 start，
  与 production_recovery ``decide_voice_action`` 的唯一放行动作一致）；
  无监听但有 manifest → ``status_verification_required``（可能是
  bootstrap 进行中/stale/归属待核，本工具刻意不探活——不执行 WSL
  /proc 探测，裁决交给生命周期工具自身的 fail-closed 语义）；其余
  listener 失败形态 → ``external_investigation_required``；
- **provider-smoke 为何仍被阻塞**：固定因果链——voice 预检 not_ready ⇒
  ``provider-smoke-export local-voice`` 前置不满足（M14-218 §5/M14-222
  §0 执行纪律：预检 not_ready 不执行 local-voice export）⇒ 聚合契约
  要求恰好三份全新单步证据 ⇒ 无新 ``provider-smoke.json`` ⇒
  release-readiness provider-smoke 门结论不变（M14-209 仍是最近一次
  完整聚合）——并给出恢复后的完整重跑序列（preflight → 三 export →
  aggregate，M14-209 口径，不复用旧 JSON）。

诚实边界（本模块全部行为面）：

- **只读**：零子进程、零文件写入、零服务生命周期变更——不启动/停止/
  重启 Docker/WSL/ASR/TTS/Ollama/语音服务；不清理/不修改 manifest
  （stale manifest 的安全清理属于 ``voice_service_control.py status``
  的既有语义，本工具只把「该跑 status」作为输出动作）；不读环境变量、
  不读 secret；
- **不探活**：manifest 记录 PID 的存活/归属核验需要 WSL ``/proc`` 探测
  （进程面子进程面）——本工具刻意不做（保持离线诊断纯度），报告恒带
  ``liveness_probed=false``，归属裁决显式让渡给生命周期工具；
- **不产生证据**：stdout-only（``--json`` 纯 JSON / 人类摘要），不写
  provider-smoke.json、不改 release-readiness 证据；诊断就绪只代表
  listener 可观测就绪，不代表 provider-smoke 已通过；
  ``production_ready`` 恒 false、``release_readiness_evidence`` 恒 false
  （报告自声明字段，测试锁定）；
- **固定词汇**：selected_path/next_action/overall 状态全部闭集枚举，
  reason/recommendation 复用 preflight 闭集（``REASONS``/
  ``REASON_TO_RECOMMENDATION``），绝不运行期拼装新词；endpoint 经
  preflight ``_classify_url`` 同款校验（userinfo/query/fragment 一律
  fail-closed 拒绝且不回显——凭据绝不进入探测与报告）；
- **无漂移**：语音槽位键（asr/tts）与 preflight voice checks、引擎名/
  端口与 ``voice_service_control.ENGINE_SPECS``、默认端点与 preflight
  ``DEFAULT_ASR_ENDPOINT``/``DEFAULT_TTS_ENDPOINT`` 均测试交叉锁定。

既有行为零改动：本工具不触碰 provider_smoke_preflight.py /
voice_service_control.py / production_recovery.py 与三个冒烟脚本的任何
判定逻辑——只读取、只引用、只推荐。
"""
from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# 复用 preflight 的探测与固定词汇（同包内部复用：_voice_health_check /
# _loopback_aware_get 是 M14-112 已评审语义，第二份实现才是漂移面）
from app.ops.provider_smoke_preflight import (
    DEFAULT_ASR_ENDPOINT,
    DEFAULT_TTS_ENDPOINT,
    _httpx_get,
    _loopback_aware_get,
    _voice_health_check,
)
from app.ops.version import locate_repository_root

#: 证据自声明（报告 tool/schema_version；与 gate 证据 schema 明确不同物）
TOOL_ID = "voice-recovery-diagnostic"
SCHEMA_VERSION = "voice-recovery-diagnostic-v1"

#: 仓库根（VERSION 标记向上查找：源码 checkout 与容器 /app 同一解析；
#: 弱契约——本工具不要求 infra/ 可见，容器布局下如实降级）
_REPOSITORY_ROOT = locate_repository_root()

#: 既有生命周期路径（repo-relative，非敏感——报告原样引用，不复制语义）
VOICE_CONTROL_SCRIPT = (
    _REPOSITORY_ROOT / "tools" / "voice" / "voice_service_control.py"
)
VOICE_CONTROL_SCRIPT_REL = "tools/voice/voice_service_control.py"
PRODUCTION_RECOVERY_SCRIPT_REL = "tools/ops/production_recovery.py"

#: 语音槽位键序（与 preflight voice checks 键一致：asr 优先——阅读顺序确定）
SLOT_KEYS = ("asr", "tts")

#: 选定恢复路径闭集（selected_path）：
#: - no_recovery_needed：listener ready，无需恢复；
#: - controlled_start：endpoint_absent 且无 manifest（stopped 形态）——
#:   与 production_recovery decide_voice_action 唯一放行的受控 start 同轨；
#: - status_verification_required：endpoint_absent 但 manifest 在场——
#:   可能 bootstrap 进行中/stale/归属待核，先跑 status 权威核验；
#: - external_investigation_required：其余 listener 失败形态（timeout/
#:   http_failure/malformed_url/……），经 status + 日志人工排查。
SELECTED_PATHS = (
    "no_recovery_needed",
    "controlled_start",
    "status_verification_required",
    "external_investigation_required",
)

#: 必需下一步动作闭集（next_action）——全部为外部运维动作，本工具永不执行
NEXT_ACTIONS = (
    "rerun_provider_smoke_preflight",
    "run_voice_status_then_start",
    "run_voice_status_only",
    "run_voice_status_and_inspect_log",
)

#: 总体状态闭集：voice_listeners_ready=双引擎 listener ready（下一步重跑
#: preflight）；voice_recovery_required=存在不就绪（恢复/核验后再重跑）
OVERALL_STATUSES = ("voice_listeners_ready", "voice_recovery_required")

#: manifest 解析结果闭集：ok=可解析且引擎相符；invalid=不可读/结构不合法/
#: 引擎不符（stale/损坏 manifest 的安全清理属于 status 的既有语义）
MANIFEST_PARSE_STATES = ("ok", "invalid")

#: builtin 引擎规格回退（生命周期工具文件缺席时的降级事实源；与
#: ENGINE_SPECS 测试交叉锁定——不构成第二生命周期管理器，只用于报告
#: 引擎名/端口/artifacts 布局的文档化默认值）
_ENGINE_FALLBACKS: dict[str, dict[str, Any]] = {
    "asr": {
        "engine": "funasr",
        "default_port": 8010,
        "artifacts_subdir": "funasr",
        "bootstrap_script": "tools/voice/bootstrap_funasr_wsl.sh",
    },
    "tts": {
        "engine": "cosyvoice",
        "default_port": 8011,
        "artifacts_subdir": "cosyvoice",
        "bootstrap_script": "tools/voice/bootstrap_cosyvoice_wsl.sh",
    },
}

#: 固定命令词汇（repo-relative，非敏感——与工具自身文档口径一致）
_STATUS_COMMAND = f"python {VOICE_CONTROL_SCRIPT_REL} status"
_START_COMMANDS = {
    "asr": f"python {VOICE_CONTROL_SCRIPT_REL} start --engine funasr",
    "tts": f"python {VOICE_CONTROL_SCRIPT_REL} start --engine cosyvoice",
}
_PREFLIGHT_COMMAND = (
    "python -m app.ops.cli provider-smoke-preflight --voice-mode local --json"
)
_EXPORT_COMMANDS = (
    (
        "python -m app.ops.cli provider-smoke-export local-voice"
        " --output <EV>/local-voice-smoke.json --json"
    ),
    (
        "python -m app.ops.cli provider-smoke-export search"
        " --output <EV>/search-smoke.json --json"
    ),
    (
        "python -m app.ops.cli provider-smoke-export llm"
        " --output <EV>/llm-smoke.json --json"
    ),
)
_AGGREGATE_COMMAND = (
    "python -m app.ops.cli provider-smoke-aggregate"
    " --search <EV>/search-smoke.json --voice <EV>/local-voice-smoke.json"
    " --llm <EV>/llm-smoke.json --voice-mode local"
    " --output <EV>/provider-smoke.json --json"
)

#: 生命周期路径注册表（既有工具 + 各自适用场景；固定文案）
_CONFIGURED_PATHS = (
    {
        "tool": VOICE_CONTROL_SCRIPT_REL,
        "role": "local 语音引擎受控生命周期（status/start/stop/restart；"
        "manifest 事实源 + 拒绝误杀/端口竞争 fail-closed 语义）",
        "applies_when": "语音槽位单独恢复（M14-222 当前阻塞形态）",
    },
    {
        "tool": PRODUCTION_RECOVERY_SCRIPT_REL,
        "role": "生产栈整体恢复编排（Docker 引擎等待 → compose 幂等 up →"
        " 健康核查 → 语音调和 decide_voice_action：stopped→受控 start，"
        "其余 leave/fail）",
        "applies_when": "整机/整栈重启后的恢复（含语音调和）",
    },
)

#: 恢复前置（固定清单——引用既有工具的文档化前置，不新增要求）
_PREREQUISITES = (
    (
        "WSL2 可用（引擎进程全部跑在 WSL 内；voice_service_control 经"
        " wsl.exe --cd 仓库根编排）"
    ),
    (
        "引擎 bootstrap 脚本在场（tools/voice/bootstrap_funasr_wsl.sh /"
        " bootstrap_cosyvoice_wsl.sh——start 生成 launcher 后 exec bootstrap）"
    ),
    (
        "模型缓存就绪或允许首次下载（funasr sensevoice / cosyvoice"
        " Fun-CosyVoice3-0.5B-2512；restart 复用缓存不重下，首次 start 的"
        " bootstrap 阶段可达数分钟，/health 200 前属启动期）"
    ),
    (
        "恢复后重跑 provider-smoke-preflight 且 voice 槽位 ready，才允许"
        " provider-smoke-export local-voice（M14-218 §5/M14-222 §0 执行纪律）"
    ),
)

_NOTES = (
    "本诊断只读、零子进程、零服务变更、不读 secret、不探活（manifest PID"
    " 存活/归属核验让渡给 voice_service_control status）；listener 就绪"
    "只代表前置可观测就绪，不代表 provider-smoke 已通过；本输出不是"
    " release-readiness 证据，不生成 provider-smoke.json；全部建议动作"
    " 为外部运维动作"
)

_LIVENESS_NOTE = (
    "本工具不执行 WSL /proc 探活——manifest 记录 PID 的存活与归属核验"
    "由 voice_service_control status 承担（其 stale 清理/fail-closed"
    " 语义不变）"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def load_lifecycle_module() -> Any | None:
    """importlib 加载既有生命周期工具（与 production_recovery 同款复用）。

    工具文件缺席（容器 /app 打包布局等）返回 None——调用方按 builtin
    回退降级并如实报告 ``engine_specs_source``，不静默冒充。模块本体纯
    标准库且 ``__main__`` 守卫，加载零副作用、零子进程。
    """
    if not VOICE_CONTROL_SCRIPT.is_file():
        return None
    spec = importlib.util.spec_from_file_location(
        "voice_service_control", VOICE_CONTROL_SCRIPT
    )
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    # sys.modules 注册必需：工具内 dataclass 的字符串注解解析经
    # sys.modules[cls.__module__]（production_recovery 同款模式）
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _engine_specs(lifecycle: Any | None) -> dict[str, dict[str, Any]]:
    """slot -> 引擎规格（优先生命周期工具 ENGINE_SPECS；缺席时 builtin 回退）。

    槽位映射按引擎名（funasr=asr / cosyvoice=tts——README 部署契约的稳定
    词汇）；取不到完整双槽位时整体回退 builtin（fail-closed，不混用两源）。
    """
    slot_by_engine = {
        spec["engine"]: slot for slot, spec in _ENGINE_FALLBACKS.items()
    }
    if lifecycle is not None:
        specs: dict[str, dict[str, Any]] = {}
        for spec in lifecycle.ENGINE_SPECS:
            slot = slot_by_engine.get(spec.name)
            if slot is None:
                continue
            specs[slot] = {
                "engine": spec.name,
                "default_port": spec.default_port,
                "artifacts_subdir": spec.artifacts_subdir,
                "bootstrap_script": spec.bootstrap_script,
            }
        if set(specs) == set(SLOT_KEYS):
            return specs
    return {slot: dict(fallback) for slot, fallback in _ENGINE_FALLBACKS.items()}


def _port_of(endpoint: str | None) -> int | None:
    """endpoint URL -> 请求端口（无显式端口/形态非法 → None，port_match 按未知）。"""
    if not endpoint:
        return None
    try:
        return urlsplit(endpoint).port
    except ValueError:
        return None


def _read_manifest_facts(
    manifest_path: Path,
    engine_name: str,
    lifecycle: Any | None,
    requested_port: int | None,
) -> dict[str, Any]:
    """只读读取 manifest 事实源（在场/解析/记录端口匹配；零写入零清理）。"""
    facts: dict[str, Any] = {
        "present": manifest_path.is_file(),
        "path": manifest_path.relative_to(_REPOSITORY_ROOT).as_posix()
        if _is_relative(manifest_path)
        else manifest_path.as_posix(),
        "parse": None,
        "recorded_port": None,
        "port_match": None,
        "pid": None,
        "started_at": None,
        "bootstrap": None,
    }
    if not facts["present"]:
        return facts
    if lifecycle is None:
        # 工具缺席但 manifest 在场：如实报在场，解析面不可用（fail-closed，
        # 不自带第二份 manifest 解析器）
        facts["parse"] = "invalid"
        facts["parse_note"] = "生命周期工具缺席，manifest 解析面不可用"
        return facts
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError:
        facts["parse"] = "invalid"
        facts["parse_note"] = "manifest 不可读（IO 问题）"
        return facts
    manifest = lifecycle.Manifest.from_json(text)
    if manifest is None or manifest.engine != engine_name:
        facts["parse"] = "invalid"
        facts["parse_note"] = "manifest 损坏或引擎不符（status 会安全清理）"
        return facts
    facts["parse"] = "ok"
    facts["recorded_port"] = manifest.port
    facts["pid"] = manifest.pid
    facts["started_at"] = manifest.started_at or None
    facts["bootstrap"] = manifest.bootstrap or None
    if requested_port is not None:
        facts["port_match"] = manifest.port == requested_port
    return facts


def _is_relative(path: Path) -> bool:
    try:
        path.relative_to(_REPOSITORY_ROOT)
        return True
    except ValueError:
        return False


def _decide(
    listener: dict[str, Any], manifest_facts: dict[str, Any]
) -> tuple[str, str, list[str], str | None]:
    """(listener, manifest) 事实 -> (selected_path, next_action, 命令序列, 端口指引)。

    纯函数、闭集输出；端口指引仅在 manifest 记录端口与请求端口不符时
    非空（口径同生命周期工具自身的 port-mismatch 拒绝提示）。
    """
    port_guidance: str | None = None
    if manifest_facts.get("port_match") is False:
        port_guidance = (
            f"manifest 记录端口 {manifest_facts['recorded_port']} 与当前请求"
            f"端口不符——生命周期工具会拒绝 start/stop/restart（防误杀/双"
            f"实例）；如需操作该实例请用 --port {manifest_facts['recorded_port']}"
        )
    if listener["status"] == "ready":
        return (
            "no_recovery_needed",
            "rerun_provider_smoke_preflight",
            [_PREFLIGHT_COMMAND],
            None,
        )
    if listener.get("reason") == "endpoint_absent":
        if manifest_facts["present"]:
            return (
                "status_verification_required",
                "run_voice_status_only",
                [_STATUS_COMMAND],
                port_guidance,
            )
        slot = listener.get("kind")
        start = _START_COMMANDS.get(str(slot))
        commands = [_STATUS_COMMAND] + ([start] if start else [])
        return (
            "controlled_start",
            "run_voice_status_then_start",
            commands,
            port_guidance,
        )
    return (
        "external_investigation_required",
        "run_voice_status_and_inspect_log",
        [_STATUS_COMMAND],
        port_guidance,
    )


def _log_hint(slot: str, artifacts_subdir: str) -> str:
    return (
        f"artifacts/voice/{artifacts_subdir}/service/service.log"
        if slot in ("asr", "tts")
        else "artifacts/voice/<engine>/service/service.log"
    )


def run_voice_recovery_diagnostic(
    *,
    asr_endpoint: str | None = None,
    tts_endpoint: str | None = None,
    get: Callable[..., tuple[int, str]] | None = None,
    clock: Callable[[], datetime] | None = None,
    lifecycle_loader: Callable[[], Any | None] | None = None,
    artifacts_root: Path | None = None,
) -> dict[str, Any]:
    """执行 local 语音恢复诊断并组装报告（stdout-only，不落盘）。

    - 端点默认值复用 preflight ``DEFAULT_*_ENDPOINT``（仓库本地部署契约）；
      传值以镜像 local-voice 冒烟实际将用的配置；
    - ``get``/``clock`` 可注入（测试零网络、零真实时间依赖）；
    - ``lifecycle_loader`` 可注入生命周期模块加载器（缺省
      :func:`load_lifecycle_module`；注入返回 None 的 loader 即模拟容器
      布局降级）；``artifacts_root`` 覆盖 manifest 根（缺省
      ``<repo>/artifacts/voice``，测试指向临时目录保证确定性）。

    返回报告 dict：engines 恒按 :data:`SLOT_KEYS` 序；overall 为
    voice_listeners_ready（双 listener ready）/ voice_recovery_required；
    报告恒携带 ``production_ready=false`` 与
    ``release_readiness_evidence=false`` 自声明。
    """
    lifecycle = (lifecycle_loader or load_lifecycle_module)()
    specs = _engine_specs(lifecycle)
    transport = _loopback_aware_get(get or _httpx_get)
    root = artifacts_root or (_REPOSITORY_ROOT / "artifacts" / "voice")
    engines: dict[str, Any] = {}
    for slot in SLOT_KEYS:
        spec = specs[slot]
        endpoint_raw = (
            asr_endpoint or DEFAULT_ASR_ENDPOINT
            if slot == "asr"
            else tts_endpoint or DEFAULT_TTS_ENDPOINT
        )
        listener = _voice_health_check(slot, endpoint_raw, get=transport)
        manifest_path = (
            root / spec["artifacts_subdir"] / "service" / "manifest.json"
        )
        manifest_facts = _read_manifest_facts(
            manifest_path, spec["engine"], lifecycle, _port_of(listener.get("endpoint"))
        )
        selected_path, next_action, commands, port_guidance = _decide(
            listener, manifest_facts
        )
        entry: dict[str, Any] = {
            "engine": spec["engine"],
            "endpoint": listener.get("endpoint"),
            "listener": listener,
            "manifest": manifest_facts,
            "liveness_probed": False,
            "liveness_note": _LIVENESS_NOTE,
            "selected_path": selected_path,
            "next_action": next_action,
            "action_commands": commands,
        }
        if port_guidance:
            entry["port_guidance"] = port_guidance
        if selected_path == "external_investigation_required":
            entry["log_hint"] = _log_hint(slot, spec["artifacts_subdir"])
        engines[slot] = entry
    blockers = [
        slot for slot in SLOT_KEYS if engines[slot]["listener"]["status"] != "ready"
    ]
    blocked = bool(blockers)
    overall = (
        "voice_recovery_required" if blocked else "voice_listeners_ready"
    )
    provider_smoke: dict[str, Any] = {
        "voice_slot_status": "ready" if not blocked else "not_ready",
        "blocked": blocked,
        "blockers": blockers,
        "rerun_sequence": [
            _PREFLIGHT_COMMAND,
            *_EXPORT_COMMANDS,
            _AGGREGATE_COMMAND,
        ],
        "rerun_sequence_note": (
            "<EV> = 全新 UTC 证据目录（gitignored artifacts/temp 下）；三份"
            " export 必须全部重新执行，不得复用旧 JSON（M14-209 口径）；"
            " preflight overall pass 且 voice ready 是 local-voice export 的"
            " 前置（M14-218 §5/M14-222 §0 执行纪律）"
        ),
    }
    if blocked:
        detail = "；".join(
            f"{slot}({engines[slot]['engine']}) listener"
            f" {engines[slot]['listener']['status']}/"
            f"{engines[slot]['listener'].get('reason')}"
            for slot in blockers
        )
        provider_smoke["why_blocked"] = (
            f"voice 预检 not_ready：{detail}——provider-smoke-export"
            " local-voice 的前置不满足（预检 not_ready 不执行该 export）；"
            "聚合契约要求恰好三份全新单步证据，voice 证据缺席则聚合不可"
            "执行；故无新 provider-smoke.json，release-readiness 的"
            " provider-smoke 门结论不变（M14-209 仍是最近一次完整聚合）"
        )
    else:
        provider_smoke["why_blocked"] = None
    return {
        "tool": TOOL_ID,
        "schema_version": SCHEMA_VERSION,
        "generated_at": (clock or _utc_now)().isoformat(),
        "topology": {"voice_mode": "local"},
        "lifecycle": {
            "manager_tool": VOICE_CONTROL_SCRIPT_REL,
            "manager_available": lifecycle is not None,
            "engine_specs_source": (
                "voice_service_control" if lifecycle is not None
                else "builtin_fallback"
            ),
            "reconciliation_tool": PRODUCTION_RECOVERY_SCRIPT_REL,
            "configured_paths": [dict(p) for p in _CONFIGURED_PATHS],
            "prerequisites": list(_PREREQUISITES),
        },
        "engines": engines,
        "provider_smoke": provider_smoke,
        "overall_status": overall,
        "production_ready": False,
        "release_readiness_evidence": False,
        "notes": _NOTES,
    }


def _httpx_get_import() -> Callable[..., tuple[int, str]]:
    """真实传输层延迟导入（与 preflight._httpx_get 同一实现，零复制）。"""
    from app.ops.provider_smoke_preflight import _httpx_get

    return _httpx_get


def diagnostic_exit_code(report: dict[str, Any]) -> int:
    """报告 -> CLI 退出码：voice_listeners_ready=0；voice_recovery_required=1。"""
    return 0 if report["overall_status"] == "voice_listeners_ready" else 1


# --- 人类可读摘要 -----------------------------------------------------------------


def format_recovery_summary(report: dict[str, Any]) -> str:
    """人类可读摘要（无凭据；固定词汇 + 明确边界声明）。"""
    lines = [
        "local 语音恢复诊断（voice-recovery-diagnostic，M14-224）",
        (
            f"完成时间: {report['generated_at']}  voice_mode=local"
            f"  规格源: {report['lifecycle']['engine_specs_source']}"
        ),
    ]
    for slot in SLOT_KEYS:
        entry = report["engines"][slot]
        listener = entry["listener"]
        line = (
            f"{slot}({entry['engine']}): {listener['status']}"
            + (f"  reason={listener['reason']}" if listener.get("reason") else "")
        )
        lines.append(line)
        manifest = entry["manifest"]
        if manifest["present"]:
            match = manifest.get("port_match")
            match_text = {
                True: "端口一致",
                False: "端口不符",
                None: "端口匹配未知",
            }[match]
            lines.append(
                f"  manifest: 在场（parse={manifest['parse']}，{match_text}，"
                f"记录端口 {manifest.get('recorded_port')}，PID "
                f"{manifest.get('pid')}）"
            )
        else:
            lines.append("  manifest: 不在场（无受控启动记录）")
        lines.append(f"  选定路径: {entry['selected_path']}")
        lines.append(f"  必需动作: {entry['next_action']}")
        for command in entry["action_commands"]:
            lines.append(f"    $ {command}")
        if entry.get("port_guidance"):
            lines.append(f"  端口指引: {entry['port_guidance']}")
        if entry.get("log_hint"):
            lines.append(f"  日志: {entry['log_hint']}")
    smoke = report["provider_smoke"]
    if smoke["blocked"]:
        lines.append(
            f"RESULT: BLOCKED——provider-smoke voice 槽位 not_ready"
            f"（blockers: {smoke['blockers']}）；{smoke['why_blocked']}"
        )
        lines.append("恢复后按以下序列重跑（全新证据，不复用旧 JSON）:")
        for command in smoke["rerun_sequence"]:
            lines.append(f"  $ {command}")
    else:
        lines.append(
            "RESULT: READY——双引擎 listener 就绪；下一步重跑预检，pass 后"
            "按三 export + aggregate 序列执行真实冒烟（预检/本诊断 pass 均"
            "不代表 provider-smoke 已通过）。"
        )
    lines.append(
        "边界: 本输出不是 release-readiness 证据（不生成 provider-smoke.json）；"
        "production_ready=false；全程只读零服务变更、不探活（归属核验经"
        " voice_service_control status）。"
    )
    lines.append("退出码: listeners ready=0 / 恢复或核验 required=1 / 参数问题=2。")
    return "\n".join(lines)
