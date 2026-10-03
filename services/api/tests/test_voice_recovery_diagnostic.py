"""M14-224 voice-recovery-diagnostic 契约测试（零网络、零子进程、零 WSL）。

覆盖矩阵：
1. CLI 注册与分发：子命令注册进主 help、main() 真实分发（monkeypatch
   模块级 _httpx_get 替身 => SystemExit）、--json stdout 纯 JSON、
   非法参数 exit 2；注册区无 --yes 执行旗标；
2. M14-222 阻塞形态复现（核心用例）：ASR/TTS 双 endpoint_absent 且无
   manifest → 双引擎 controlled_start（先 status 权威核验再经受控工具
   start，与 production_recovery decide_voice_action 的唯一放行动作
   同轨）、provider_smoke.blocked=true、why_blocked 含完整因果链
   （local-voice export 前置不满足 → 聚合三输入 → 门结论不变）；
3. 恢复路径闭集矩阵：listener ready（no_recovery_needed）/ manifest
   在场（status_verification_required——不推荐盲目 start）/ 端口不符
   （port_guidance 给 --port 指引）/ manifest 损坏（parse=invalid，
   fail-closed 不降级为 controlled_start）/ 非 endpoint_absent 失败
   （external_investigation_required + 日志指引）；
4. manifest 事实只读快照：在场/端口匹配/PID/started_at/bootstrap 透出；
   生命周期工具缺席（容器布局）时 engine_specs_source=builtin_fallback、
   manifest 解析面如实不可用；builtin 回退与真实 ENGINE_SPECS 交叉锁定
   （引擎名/默认端口/artifacts 子目录/bootstrap 脚本零漂移）；
5. listener 语义零漂移：诊断的 listener 子报告与
   run_provider_smoke_preflight 的 voice.checks 结构全等（同 FakeGet）；
   默认端点探测 8010/8011 服务根 /health；loopback 探测恒
   trust_env=False；含 userinfo 的 endpoint fail-closed 拒绝且凭据零泄漏；
6. 只读守卫（源码级 ast 扫描）：模块零 subprocess/os.environ/getenv/
   文件写入面；CLI 处理函数零文件写入；测试运行期零真实网络拨号；
7. 非门证据自声明：报告恒 production_ready=false、
   release_readiness_evidence=false；喂给 release-readiness 的
   provider-smoke 门评估器必须 MalformedEvidence 拒收；人类摘要含边界
   声明；liveness_probed 恒 false（探活让渡给 voice_service_control
   status）；
8. 重跑序列：voice 就绪后的序列 = preflight → 三 export → aggregate，
   提示「全新证据、不复用旧 JSON」（M14-209 口径）；
9. 退出码：listeners ready=0 / 恢复 required=1；输出确定性（同输入两次
   运行 JSON 全等）；selected_path/next_action 恒为闭集成员。

全部测试只用进程内替身与临时目录 manifest——不调用任何真实 provider/
外网端点、不连数据库、不发网络请求、不启动 WSL/引擎。
"""
from __future__ import annotations

import ast
import functools
import importlib.util
import json
import socket
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.ops import cli as cli_module
from app.ops import voice_recovery_diagnostic as vrd
from app.ops.evidence_kit import MalformedEvidence
from app.ops.release_readiness import _eval_provider_smoke
from app.ops.voice_recovery_diagnostic import (
    MANIFEST_PARSE_STATES,
    NEXT_ACTIONS,
    OVERALL_STATUSES,
    SELECTED_PATHS,
    SLOT_KEYS,
    diagnostic_exit_code,
    format_recovery_summary,
    load_lifecycle_module,
    run_voice_recovery_diagnostic,
)

FIXED_CLOCK = datetime(2026, 10, 3, 1, 2, 3, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[3]
VOICE_CONTROL = REPO_ROOT / "tools" / "voice" / "voice_service_control.py"


# ---------- 进程内替身（记录调用，零网络） ----------


class FakeGet:
    """按 URL 子串路由响应的替身传输层（与 preflight 契约测试同款）。"""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, **kwargs: Any) -> tuple[int, str]:
        self.calls.append((url, dict(kwargs)))
        for pattern, outcome in self.routes.items():
            if pattern in url:
                if isinstance(outcome, Exception):
                    raise outcome
                status, body = outcome
                return status, body
        raise AssertionError(f"FakeGet 未路由的 URL: {url}")


def _absent() -> Any:
    from app.ops.provider_smoke_preflight import PreflightTransportError

    return PreflightTransportError("endpoint_absent", "ConnectError")


def _both_absent_get() -> FakeGet:
    return FakeGet({"8010/health": _absent(), "8011/health": _absent()})


def _both_ready_get() -> FakeGet:
    return FakeGet(
        {"8010/health": (200, '{"model": "sensevoice"}'),
         "8011/health": (200, '{"model": "Fun-CosyVoice3-0.5B-2512"}')}
    )


@functools.lru_cache(maxsize=1)
def _real_lifecycle() -> Any:
    """测试进程内加载真实 voice_service_control（只读词汇交叉锁定用）。"""
    assert VOICE_CONTROL.is_file(), f"生命周期工具缺席: {VOICE_CONTROL}"
    spec = importlib.util.spec_from_file_location(
        "vrd_test_voice_service_control", VOICE_CONTROL
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _diagnose(get: Any, tmp_path: Path, **kwargs: Any) -> dict[str, Any]:
    """确定性运行：固定时钟 + 临时 manifest 根 + 真实生命周期词汇。"""
    return run_voice_recovery_diagnostic(
        get=get,
        clock=lambda: FIXED_CLOCK,
        artifacts_root=tmp_path / "artifacts-voice",
        lifecycle_loader=_real_lifecycle,
        **kwargs,
    )


def _write_manifest(
    tmp_path: Path,
    engine: str,
    *,
    port: int | None = None,
    pid: int = 4242,
    corrupt: bool = False,
) -> Path:
    """在临时 manifest 根写入一份（真实 Manifest 序列化形态的）事实源。"""
    module = _real_lifecycle()
    spec = next(s for s in module.ENGINE_SPECS if s.name == engine)
    directory = tmp_path / "artifacts-voice" / spec.artifacts_subdir / "service"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    if corrupt:
        path.write_text("{ not-json", encoding="utf-8")
        return path
    manifest = module.Manifest(
        engine=engine,
        pid=pid,
        port=port if port is not None else spec.default_port,
        started_at="2026-10-02T22:00:00+08:00",
        bootstrap=spec.bootstrap_script,
        cmd_markers=list(spec.cmd_markers),
        workdir="/mnt/wsl-workspace",
        log=f"artifacts/voice/{spec.artifacts_subdir}/service/service.log",
    )
    path.write_text(manifest.to_json(), encoding="utf-8")
    return path


def _closed_vocab(report: dict[str, Any]) -> None:
    """闭集词汇守卫：selected_path/next_action/parse/overall 全为枚举成员。"""
    assert report["overall_status"] in OVERALL_STATUSES
    for entry in report["engines"].values():
        assert entry["selected_path"] in SELECTED_PATHS
        assert entry["next_action"] in NEXT_ACTIONS
        parse = entry["manifest"].get("parse")
        assert parse is None or parse in MANIFEST_PARSE_STATES


# ---------- 1. CLI 注册与分发 ----------


def test_subcommand_registered_in_main_help(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "voice-recovery-diagnostic", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "voice-recovery-diagnostic" in out
    assert "--asr-endpoint" in out and "--tts-endpoint" in out and "--json" in out


def test_cli_invalid_argument_exit_2(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "voice-recovery-diagnostic", "--bogus"])
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 2


def test_cli_dispatched_via_main(monkeypatch, capsys, tmp_path) -> None:
    """main() 真实分发：M14-222 形态 → exit 1、stdout 纯 JSON 报告。"""
    monkeypatch.setattr(sys, "argv", ["cli", "voice-recovery-diagnostic", "--json"])
    monkeypatch.setattr(vrd, "_httpx_get", _both_absent_get())
    monkeypatch.setattr(vrd, "_REPOSITORY_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        cli_module.main()
    assert exc.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["tool"] == "voice-recovery-diagnostic"
    assert report["overall_status"] == "voice_recovery_required"
    assert report["engines"]["asr"]["selected_path"] == "controlled_start"


def test_cli_parser_block_has_no_yes_flag() -> None:
    """parser 注册区（p_vrd 块）不得有 --yes 旗标：诊断是只读面。"""
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    block = source.split("p_vrd = sub.add_parser(")[1].split(
        "p_pg = sub.add_parser("
    )[0]
    assert '"--yes"' not in block


# ---------- 2. M14-222 阻塞形态（核心用例） ----------


def test_m14_222_shape_maps_to_controlled_start(tmp_path) -> None:
    """ASR/TTS 双 endpoint_absent 且无 manifest（M14-222 实证形态）：
    双引擎 stopped → controlled_start（status 先行 + 受控工具 start）。"""
    report = _diagnose(_both_absent_get(), tmp_path)
    _closed_vocab(report)
    assert report["overall_status"] == "voice_recovery_required"
    assert diagnostic_exit_code(report) == 1
    for slot, engine in (("asr", "funasr"), ("tts", "cosyvoice")):
        entry = report["engines"][slot]
        assert entry["engine"] == engine
        assert entry["listener"]["status"] == "not_ready"
        assert entry["listener"]["reason"] == "endpoint_absent"
        assert entry["manifest"]["present"] is False
        assert entry["selected_path"] == "controlled_start"
        assert entry["next_action"] == "run_voice_status_then_start"
        assert entry["action_commands"] == [
            "python tools/voice/voice_service_control.py status",
            f"python tools/voice/voice_service_control.py start --engine {engine}",
        ]
        assert entry["liveness_probed"] is False


def test_m14_222_shape_reports_blocker_chain(tmp_path) -> None:
    """provider_smoke 阻塞因果链：local-voice export 前置 → 聚合三输入 →
    门结论不变（M14-209 仍是最近一次完整聚合）。"""
    report = _diagnose(_both_absent_get(), tmp_path)
    smoke = report["provider_smoke"]
    assert smoke["blocked"] is True
    assert smoke["blockers"] == ["asr", "tts"]
    assert smoke["voice_slot_status"] == "not_ready"
    why = smoke["why_blocked"]
    assert "provider-smoke-export local-voice" in why
    assert "三份" in why and "聚合" in why
    assert "M14-209" in why
    assert "asr(funasr) listener not_ready/endpoint_absent" in why
    assert "tts(cosyvoice) listener not_ready/endpoint_absent" in why


def test_m14_222_shape_human_summary(tmp_path) -> None:
    report = _diagnose(_both_absent_get(), tmp_path)
    summary = format_recovery_summary(report)
    assert "RESULT: BLOCKED" in summary
    assert "controlled_start" in summary
    assert "voice_service_control.py start" in summary
    assert "不是 release-readiness 证据" in summary
    assert "production_ready=false" in summary


# ---------- 3. 恢复路径闭集矩阵 ----------


def test_listeners_ready_maps_to_preflight_rerun(tmp_path) -> None:
    report = _diagnose(_both_ready_get(), tmp_path)
    _closed_vocab(report)
    assert report["overall_status"] == "voice_listeners_ready"
    assert diagnostic_exit_code(report) == 0
    smoke = report["provider_smoke"]
    assert smoke["blocked"] is False
    assert smoke["blockers"] == []
    assert smoke["voice_slot_status"] == "ready"
    assert smoke["why_blocked"] is None
    for slot in SLOT_KEYS:
        entry = report["engines"][slot]
        assert entry["selected_path"] == "no_recovery_needed"
        assert entry["next_action"] == "rerun_provider_smoke_preflight"
        assert entry["action_commands"] == [
            (
                "python -m app.ops.cli provider-smoke-preflight"
                " --voice-mode local --json"
            )
        ]
    summary = format_recovery_summary(report)
    assert "RESULT: READY" in summary
    assert "不代表 provider-smoke 已通过" in summary


def test_partial_ready_blocks_only_failed_slot(tmp_path) -> None:
    get = FakeGet(
        {"8010/health": (200, "{}"), "8011/health": _absent()}
    )
    report = _diagnose(get, tmp_path)
    _closed_vocab(report)
    assert report["provider_smoke"]["blockers"] == ["tts"]
    assert report["engines"]["asr"]["selected_path"] == "no_recovery_needed"
    assert report["engines"]["tts"]["selected_path"] == "controlled_start"
    assert diagnostic_exit_code(report) == 1


def test_manifest_present_requires_status_verification(tmp_path) -> None:
    """无监听但有 manifest（M14-145 enforce 回合的 managed-starting 形态）：
    只推荐 status（权威核验 + 安全清理 stale），绝不盲目推荐 start。"""
    _write_manifest(tmp_path, "funasr")
    _write_manifest(tmp_path, "cosyvoice")
    report = _diagnose(_both_absent_get(), tmp_path)
    _closed_vocab(report)
    for slot in SLOT_KEYS:
        entry = report["engines"][slot]
        assert entry["selected_path"] == "status_verification_required"
        assert entry["next_action"] == "run_voice_status_only"
        assert entry["action_commands"] == [
            "python tools/voice/voice_service_control.py status"
        ]
        assert entry["manifest"]["present"] is True
        assert entry["manifest"]["parse"] == "ok"
        assert entry["manifest"]["port_match"] is True
        assert entry["manifest"]["pid"] == 4242
        assert entry["manifest"]["bootstrap"].startswith("tools/voice/bootstrap_")


def test_manifest_port_mismatch_gives_port_guidance(tmp_path) -> None:
    """manifest 记录端口 ≠ 请求端口：口径同生命周期工具的 port-mismatch
    拒绝提示（--port <记录端口>），不推荐直接 start。"""
    _write_manifest(tmp_path, "funasr", port=9010)
    report = _diagnose(_both_absent_get(), tmp_path)
    entry = report["engines"]["asr"]
    assert entry["manifest"]["port_match"] is False
    assert entry["manifest"]["recorded_port"] == 9010
    assert entry["selected_path"] == "status_verification_required"
    assert "--port 9010" in entry["port_guidance"]
    # tts 无 manifest：不受 asr 端口不符影响
    assert report["engines"]["tts"]["selected_path"] == "controlled_start"
    assert "port_guidance" not in report["engines"]["tts"]


def test_corrupt_manifest_stays_fail_closed(tmp_path) -> None:
    """manifest 损坏：parse=invalid（status 会安全清理），不降级为
    controlled_start——在场事实就是「先核验」。"""
    _write_manifest(tmp_path, "funasr", corrupt=True)
    report = _diagnose(_both_absent_get(), tmp_path)
    entry = report["engines"]["asr"]
    assert entry["manifest"]["present"] is True
    assert entry["manifest"]["parse"] == "invalid"
    assert entry["selected_path"] == "status_verification_required"
    assert entry["next_action"] == "run_voice_status_only"


def test_non_absent_listener_failure_requires_investigation(tmp_path) -> None:
    """endpoint_timeout / http_failure 等其余失败形态：external_investigation
    + 日志指引（归属/升温状态经 status + service.log 人工排查）。"""
    from app.ops.provider_smoke_preflight import PreflightTransportError

    timeout = PreflightTransportError("endpoint_timeout", "ReadTimeout")
    get = FakeGet(
        {"8010/health": timeout, "8011/health": (503, '{"status": "loading"}')}
    )
    report = _diagnose(get, tmp_path)
    _closed_vocab(report)
    asr_entry = report["engines"]["asr"]
    tts_entry = report["engines"]["tts"]
    assert asr_entry["listener"]["reason"] == "endpoint_timeout"
    assert tts_entry["listener"]["reason"] == "http_failure"
    for entry in (asr_entry, tts_entry):
        assert entry["selected_path"] == "external_investigation_required"
        assert entry["next_action"] == "run_voice_status_and_inspect_log"
        assert entry["log_hint"].endswith("service/service.log")


# ---------- 4. manifest 快照 / builtin 回退 / 词汇交叉锁定 ----------


def test_builtin_fallback_matches_real_engine_specs(tmp_path) -> None:
    """builtin 回退与真实 ENGINE_SPECS 零漂移（引擎名/默认端口/artifacts
    子目录/bootstrap 脚本）——回退不是第二生命周期管理器，只是同词汇的
    文档化默认值。"""
    module = _real_lifecycle()
    report = _diagnose(_both_absent_get(), tmp_path)
    for slot in SLOT_KEYS:
        spec = next(
            s for s in module.ENGINE_SPECS
            if s.name == report["engines"][slot]["engine"]
        )
        fallback = vrd._ENGINE_FALLBACKS[slot]
        assert fallback["engine"] == spec.name
        assert fallback["default_port"] == spec.default_port
        assert fallback["artifacts_subdir"] == spec.artifacts_subdir
        assert fallback["bootstrap_script"] == spec.bootstrap_script
    assert report["lifecycle"]["engine_specs_source"] == "voice_service_control"
    assert report["lifecycle"]["manager_available"] is True


def test_lifecycle_module_unavailable_degrades_honestly(tmp_path) -> None:
    """生命周期工具缺席（容器 /app 布局口径）：builtin 回退 + manifest
    解析面如实不可用（fail-closed，不静默冒充、不自带第二解析器）。"""
    report = run_voice_recovery_diagnostic(
        get=_both_absent_get(),
        clock=lambda: FIXED_CLOCK,
        artifacts_root=tmp_path / "artifacts-voice",
        lifecycle_loader=lambda: None,
    )
    _closed_vocab(report)
    assert report["lifecycle"]["manager_available"] is False
    assert report["lifecycle"]["engine_specs_source"] == "builtin_fallback"
    # 工具缺席但 manifest 文件在场：parse=invalid + 明确 note
    _write_manifest(tmp_path, "funasr")
    report_with_file = run_voice_recovery_diagnostic(
        get=_both_absent_get(),
        clock=lambda: FIXED_CLOCK,
        artifacts_root=tmp_path / "artifacts-voice",
        lifecycle_loader=lambda: None,
    )
    facts = report_with_file["engines"]["asr"]["manifest"]
    assert facts["present"] is True
    assert facts["parse"] == "invalid"
    assert "不可用" in facts["parse_note"]
    assert report_with_file["engines"]["asr"]["selected_path"] == (
        "status_verification_required"
    )


def test_lifecycle_section_documents_configured_paths(tmp_path) -> None:
    """「选定配置路径」注册表：两条既有生命周期路径 + 前置清单。"""
    report = _diagnose(_both_absent_get(), tmp_path)
    lifecycle = report["lifecycle"]
    assert lifecycle["manager_tool"] == "tools/voice/voice_service_control.py"
    assert lifecycle["reconciliation_tool"] == "tools/ops/production_recovery.py"
    tools = [p["tool"] for p in lifecycle["configured_paths"]]
    assert tools == [
        "tools/voice/voice_service_control.py",
        "tools/ops/production_recovery.py",
    ]
    joined = "\n".join(lifecycle["prerequisites"])
    assert "WSL2" in joined
    assert "bootstrap_funasr_wsl.sh" in joined and "bootstrap_cosyvoice_wsl.sh" in joined
    assert "provider-smoke-preflight" in joined


def test_load_lifecycle_module_real_run(tmp_path) -> None:
    """默认加载器：真实 checkout 下可加载（词汇源可用）；缺席时返回 None。"""
    module = load_lifecycle_module()
    assert module is not None
    assert hasattr(module, "ENGINE_SPECS")
    absent = vrd.VOICE_CONTROL_SCRIPT.with_name("nonexistent.py")
    original = vrd.VOICE_CONTROL_SCRIPT
    vrd.VOICE_CONTROL_SCRIPT = absent
    try:
        assert load_lifecycle_module() is None
    finally:
        vrd.VOICE_CONTROL_SCRIPT = original


# ---------- 5. listener 语义零漂移（与 preflight 交叉锁定） ----------


def _preflight_routes(get: FakeGet) -> FakeGet:
    """给诊断替身补齐 preflight 需要的 search/llm 路由（同 FakeGet 实例）。"""
    get.routes.setdefault(
        "search?", (200, json.dumps({"results": [1, 2, 3], "unresponsive_engines": []}))
    )
    get.routes.setdefault("api/ps", (200, json.dumps({"models": []})))
    return get


def test_listener_report_equals_preflight_voice_checks(tmp_path) -> None:
    """诊断的 engines.<slot>.listener 与 preflight 的 voice.checks 全等——
    同一 _voice_health_check 实现，零第二套探测语义。"""
    from app.ops.provider_smoke_preflight import run_provider_smoke_preflight

    shared = _preflight_routes(_both_ready_get())
    preflight = run_provider_smoke_preflight(get=shared, clock=lambda: FIXED_CLOCK)
    diagnostic = _diagnose(_both_ready_get(), tmp_path)
    assert diagnostic["engines"]["asr"]["listener"] == preflight["providers"][
        "voice"
    ]["checks"]["asr"]
    assert diagnostic["engines"]["tts"]["listener"] == preflight["providers"][
        "voice"
    ]["checks"]["tts"]
    # blocked 形态同样全等（reason 归因一致）
    shared_blocked = _preflight_routes(_both_absent_get())
    preflight_blocked = run_provider_smoke_preflight(
        get=shared_blocked, clock=lambda: FIXED_CLOCK
    )
    diagnostic_blocked = _diagnose(_both_absent_get(), tmp_path)
    assert (
        diagnostic_blocked["engines"]["asr"]["listener"]["reason"]
        == preflight_blocked["providers"]["voice"]["checks"]["asr"]["reason"]
        == "endpoint_absent"
    )


def test_default_endpoints_probe_service_root_health(tmp_path) -> None:
    """默认端点：探测打服务根 /health（剥 /v1——与冒烟脚本同契约）。"""
    get = _both_absent_get()
    _diagnose(get, tmp_path)
    urls = [url for url, _ in get.calls]
    assert "http://127.0.0.1:8010/health" in urls
    assert "http://127.0.0.1:8011/health" in urls


def test_loopback_probe_trust_env_false(tmp_path) -> None:
    """loopback 探测恒 trust_env=False（代理不得劫持本机探测）。"""
    get = _both_ready_get()
    _diagnose(get, tmp_path)
    for url, kwargs in get.calls:
        assert kwargs.get("trust_env") is False, (url, kwargs)


def test_custom_endpoints_flow_through(tmp_path) -> None:
    get = FakeGet({"9010/health": (200, "{}"), "9011/health": _absent()})
    report = _diagnose(
        get,
        tmp_path,
        asr_endpoint="http://127.0.0.1:9010/v1",
        tts_endpoint="http://127.0.0.1:9011/v1",
    )
    assert report["engines"]["asr"]["endpoint"] == "http://127.0.0.1:9010/v1"
    assert report["engines"]["asr"]["selected_path"] == "no_recovery_needed"
    assert report["provider_smoke"]["blockers"] == ["tts"]


def test_userinfo_endpoint_rejected_without_leak(tmp_path) -> None:
    """含 userinfo 的 endpoint：fail-closed 拒绝（malformed_url）、endpoint
    不回显、凭据零泄漏（复用 preflight _classify_url 语义）。"""
    secret = "super-secret-token"
    get = FakeGet({"8011/health": _absent()})  # tts 默认端点照常探测
    report = _diagnose(
        get,
        tmp_path,
        asr_endpoint=f"http://user:{secret}@127.0.0.1:8010/v1",
    )
    entry = report["engines"]["asr"]
    assert entry["listener"]["status"] == "not_ready"
    assert entry["listener"]["reason"] == "malformed_url"
    assert entry["endpoint"] is None
    assert secret not in json.dumps(report, ensure_ascii=False)
    # asr 先于一切探测拒绝（零请求发出）；tts 正常探测
    assert all("8010" not in url for url, _ in get.calls)
    assert any("8011/health" in url for url, _ in get.calls)


# ---------- 6. 只读守卫（源码级） ----------


def test_module_source_is_read_only() -> None:
    """ast 扫描：模块零子进程/零环境读取/零文件写入面（read_text/is_file
    只读探测允许；importlib 加载生命周期词汇为 production_recovery 同款
    复用模式，非子进程）。"""
    source = Path(vrd.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename="voice_recovery_diagnostic.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "subprocess", "模块不得 import subprocess"
        if isinstance(node, ast.ImportFrom):
            assert node.module != "subprocess", "模块不得 import subprocess"
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else ""
            )
            assert name not in {
                "popen", "system", "mkdir", "write_text", "write_bytes",
                "unlink", "rmtree", "kill", "replace", "rename",
            }, f"只读模块禁止文件写入/子进程调用: {name}"
    for banned in ("os.environ", "getenv"):
        assert banned not in source, f"源码不得出现 {banned}"


def test_cli_handler_writes_no_files(monkeypatch, tmp_path) -> None:
    """CLI 处理函数不产生任何文件系统写入（stdout-only 契约）。"""
    monkeypatch.setattr(vrd, "_httpx_get", _both_absent_get())
    monkeypatch.setattr(vrd, "_REPOSITORY_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    exit_code = cli_module._run_voice_recovery_diagnostic(
        type(
            "Args",
            (),
            {"asr_endpoint": None, "tts_endpoint": None, "as_json": False},
        )()
    )
    assert exit_code == 1
    assert not list(tmp_path.rglob("*"))


def test_no_real_network_in_test_run(monkeypatch, tmp_path) -> None:
    """防回归：任何真实 socket 拨号即失败（本套件只允许进程内替身）。"""
    def _forbid(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("契约测试禁止真实网络拨号")

    monkeypatch.setattr(socket, "create_connection", _forbid)
    _diagnose(_both_absent_get(), tmp_path)


# ---------- 7. 非门证据自声明与边界 ----------


def test_report_is_not_release_readiness_evidence(tmp_path) -> None:
    report = _diagnose(_both_absent_get(), tmp_path)
    assert report["production_ready"] is False
    assert report["release_readiness_evidence"] is False
    assert report["tool"] == "voice-recovery-diagnostic"
    assert report["schema_version"] == "voice-recovery-diagnostic-v1"
    assert report["topology"] == {"voice_mode": "local"}
    with pytest.raises(MalformedEvidence):
        _eval_provider_smoke(dict(report), tmp_path, {})


def test_liveness_probed_false_with_note(tmp_path) -> None:
    """探活让渡声明：每引擎恒带 liveness_probed=false + 指向 status 的说明。"""
    report = _diagnose(_both_absent_get(), tmp_path)
    for entry in report["engines"].values():
        assert entry["liveness_probed"] is False
        assert "voice_service_control status" in entry["liveness_note"]


def test_rerun_sequence_is_full_m14_209_shape(tmp_path) -> None:
    """重跑序列：preflight → 三 export（local-voice/search/llm）→ aggregate，
    提示全新证据目录、不复用旧 JSON。"""
    report = _diagnose(_both_ready_get(), tmp_path)
    sequence = report["provider_smoke"]["rerun_sequence"]
    assert sequence[0].startswith("python -m app.ops.cli provider-smoke-preflight")
    exports = [c for c in sequence if "provider-smoke-export" in c]
    assert len(exports) == 3
    assert any("local-voice" in c for c in exports)
    assert any("export search" in c for c in exports)
    assert any("export llm" in c for c in exports)
    assert sequence[-1].startswith("python -m app.ops.cli provider-smoke-aggregate")
    note = report["provider_smoke"]["rerun_sequence_note"]
    assert "不得复用旧 JSON" in note
    assert "三份" in note


# ---------- 8. 退出码与确定性 ----------


def test_exit_code_vocabulary() -> None:
    assert diagnostic_exit_code({"overall_status": "voice_listeners_ready"}) == 0
    assert diagnostic_exit_code({"overall_status": "voice_recovery_required"}) == 1


def test_report_deterministic(tmp_path) -> None:
    """同输入两次运行 JSON 全等（固定时钟；报告无随机/时间漂移面）。"""
    first = _diagnose(_both_absent_get(), tmp_path)
    second = _diagnose(_both_absent_get(), tmp_path)
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
    assert first["generated_at"] == FIXED_CLOCK.isoformat()
    assert list(first["engines"]) == list(SLOT_KEYS) == ["asr", "tts"]


def test_closed_sets_are_frozen_vocabulary() -> None:
    """闭集枚举自恰：SELECTED_PATHS/NEXT_ACTIONS/OVERALL_STATUSES 无重复；
    NEXT_ACTIONS 与 SELECTED_PATHS 的推荐组合在实现里确定映射。"""
    assert len(set(SELECTED_PATHS)) == len(SELECTED_PATHS)
    assert len(set(NEXT_ACTIONS)) == len(NEXT_ACTIONS)
    assert len(set(OVERALL_STATUSES)) == len(OVERALL_STATUSES)
