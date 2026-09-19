"""M11-16 provider-smoke-evidence：provider 冒烟证据导出与聚合。

覆盖矩阵：
1. CLI 注册与分发：两个子命令注册进主 help、必填参数缺失 exit 2、非法
   provider 枚举 exit 2、main() 真实分发（fake runner pass => SystemExit 0）、
   parser 注册区无 --yes 执行旗标；M14-70 slice 4：export choices 与
   PROVIDERS 一致（四个注册 provider 全部可经 main() 真实分发）；
2. 映射稳定性：PROVIDERS 注册条目（provider/脚本相对路径/step/gate_key）与
   cutover-rehearsal STEPS 文件名、release-readiness SMOKE_PROVIDERS 键序与
   provider-smoke gate 精确文件名交叉锁定，冒烟脚本真实存在于仓库根；
   M14-70 slice 2：local-voice 注册条目（step/脚本/文件名/gate_key、脚本
   真实存在、不接入 rehearsal STEPS 与聚合槽位）与 cloud-voice 原条目锁定；
3. 单步导出语义（fake runner，零真实子进程）：退出码 0 => pass/exit 0 证据
   落盘；非零 => fail/exit 1 失败证据照常落盘（如实记录不伪装 pass）；runner
   抛 OSError / bash 不可用 / 脚本缺失 => exit 2 不写证据不打印结论；未知
   provider 拒绝；runner 收到的恰为 [bash, 脚本相对路径] 列表参数；
4. runner 调用形态（真实 _run_smoke_script + ``python -c`` 探针，无 bash
   依赖、零网络）：cwd=仓库根、子进程整体继承当前环境（探针回读环境
   marker）、check=False（非零退出码透传不抛）、不捕获 stdout/stderr；
5. 路径与 symlink fail-closed：输出越界（非 artifacts/temp）、文件名不精确
   （含把门文件名当单步输出）、输出已存在且是目录、输出 symlink / 聚合输入
   symlink / 中间目录组件 symlink 一律 exit 2 且先于 runner（不运行冒烟）；
6. 原子写：写入失败（磁盘满形态）exit 2、旧文件字节原样、无 .tmp 残留、
   不打印结论；正常写无 .tmp 残留；
7. 聚合校验：三份合法 => exit 0；任一 fail => exit 1 照常落盘；非本工具
   导出形态（tool 不匹配、schema_version 不符、step 槽位错位、executed 非
   true、result=not_executed/未知、非法 JSON、顶层非对象）fail-closed
   exit 2 不写输出；聚合输入 exact schema 回归（Codex 验收整改）——顶层
   键集合缺任一字段/多一个额外字段、pass 但 exit_code 非 0、fail 但
   exit_code=0、无效或 naive 或非字符串时间、completed 早于 started、负
   duration、bool 伪装 int/非 int 形态一律 exit 2 不写输出且输入字节不变；
   等时零耗时形态（真实导出可产生）放行；聚合输入越界/不存在/是目录拒绝；
   输出不得等于任何输入（.. 折叠/Windows 大小写变体不构成绕过）、同一输入
   不得重复传两个槽位、输出文件名必须恰为 provider-smoke.json；槽位不齐
   拒绝；
   M14-70 slice 3 拓扑聚合：voice_mode keyword-only 默认 "cloud" 向后兼容
   （不传与显式 cloud 输出一致）；local 拓扑换用 local-voice 槽位、
   cloud/hybrid 恒用 cloud-voice 槽位（多/缺/错放语音证据一律拒绝）；
   非法 mode 先于一切输入处理拒绝；聚合输出新增 topology.voice_mode 与
   每 provider 的 evidence_step（精确 step id），providers 每项仍仅
   executed/result/evidence_step；
   M14-70 slice 4 CLI 语音拓扑旗标：--voice 与 --cloud-voice 恰好其一
   （同给/都不给 exit 2 不写输出）；--voice-mode local 语音证据必须经
   --voice 提供（legacy --cloud-voice 误用 exit 2）；local/hybrid/cloud
   经 main() 真实分发聚合成功且 topology 如实自声明；legacy 无旗标调用
   输出不变；新形态 --voice 与 legacy --cloud-voice 在 cloud 拓扑下输出
   等价（除 generated_at）；--voice-mode argparse 枚举拒绝非法值；
8. 消费：单步证据直过 cutover-rehearsal 三冒烟步评估器（pass/fail）；
   聚合证据直过 release-readiness provider-smoke 门评估器（全 pass / 任一
   fail => blocked）；落盘文件放进证据目录经完整 run_cutover_rehearsal /
   run_release_readiness 消费同语义；
9. 零敏感信息落盘：单步证据顶层键恰为白名单（tool + 任务契约八字段，
   与 STEP_EVIDENCE_KEYS 交叉锁定）、聚合 providers 每项仅
   executed/result/evidence_step；
   毒化敏感环境变量（含 fake endpoint/key/模型名/查询词/音频路径 marker）
   不被读取——模块不读取 provider 端点/密钥/模型/查询/音频等敏感环境槽位
   （唯一环境访问是 shutil.which 经 PATH 解析 bash，子进程整体继承环境）、
   stdout/stderr/摘要/落盘文件零泄漏；find_sensitive_key/
   find_embedded_credential 零命中；
10. 源码级守卫（源码口径，非行为面零环境访问声明——shutil.which 在库
    内部经 PATH 查找 bash）：模块源码零 os.environ/零 getenv/零 DB/零网络
    库引用；唯一的 subprocess.run 调用形态 ast 锁定（列表参数、cwd=仓库根、
    check=False、无 env=/stdout=/stderr=/capture_output=/shell=）；
11. --json：stdout 纯 JSON（提示走 stderr）、与落盘文件逐字段一致；
12. clock 注入：起止时间与耗时确定性、负耗时钳 0。

全部测试只用临时目录、本地文件与 ``python -c`` 探针子进程——不调用任何
真实 provider/外网端点、不连接数据库、不发网络请求。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ops import cli as cli_module
from app.ops import provider_smoke_evidence as pse
from app.ops.cutover_rehearsal import EVALUATORS as REHEARSAL_EVALUATORS
from app.ops.cutover_rehearsal import STEPS, run_cutover_rehearsal
from app.ops.evidence_kit import find_embedded_credential, find_sensitive_key
from app.ops.provider_smoke_evidence import (
    AGGREGATE_PROVIDERS,
    GATE_OUTPUT_FILE,
    PROVIDERS,
    STEP_EVIDENCE_KEYS,
    STEP_OUTPUT_FILES,
    TOOL_ID,
    VOICE_MODES,
    ProviderSmokeInputError,
    build_provider_smoke_evidence,
    build_step_evidence,
    format_aggregate_summary,
    format_step_summary,
)
from app.ops.release_readiness import (
    EVALUATORS as READINESS_EVALUATORS,
)
from app.ops.release_readiness import (
    GATES,
    SMOKE_PROVIDERS,
    run_release_readiness,
)

#: 敏感 marker：模拟 endpoint/key/模型名/查询词/音频路径——任何输出
#: （stdout / stderr / 摘要 / 落盘文件）都不得包含
ENDPOINT_MARKER = "https://prod-endpoint-7e01.example.internal"
KEY_MARKER = "PROD-KEY-7e02"
MODEL_MARKER = "prod-model-7e03"
QUERY_MARKER = "prod-query-7e04"
AUDIO_MARKER = "/prod/audio/voice-7e05.wav"
ALL_MARKERS = (
    ENDPOINT_MARKER,
    KEY_MARKER,
    MODEL_MARKER,
    QUERY_MARKER,
    AUDIO_MARKER,
)


# --- fixture 与构造 helper（零真实子进程、零网络） -------------------------------


@pytest.fixture
def fake_runner(monkeypatch):
    """安装 fake 子进程 runner：替换真实 ``_run_smoke_script`` 与 bash 查找，
    记录每次调用的 argv 形态。返回 (install(returncode), calls)。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        pse.shutil, "which", lambda name: "/fake/bin/bash" if name == "bash" else None
    )

    def install(returncode: int) -> None:
        def run(argv):
            calls.append(list(argv))
            return subprocess.CompletedProcess(args=list(argv), returncode=returncode)

        monkeypatch.setattr(pse, "_run_smoke_script", run)

    return install, calls


def _artifacts(tmp_path: Path) -> Path:
    directory = tmp_path / "artifacts"
    directory.mkdir(exist_ok=True)
    return directory


def _export_cli(provider: str, output: Path, *, as_json: bool = False) -> int:
    return cli_module._run_provider_smoke_export(
        SimpleNamespace(provider=provider, output=str(output), as_json=as_json)
    )


def _aggregate_cli(
    search: Path,
    cloud_voice: Path | None,
    llm: Path,
    output: Path,
    *,
    as_json: bool = False,
    voice: Path | None = None,
    voice_mode: str = "cloud",
) -> int:
    """handler 直调聚合 CLI。默认参数即 legacy argparse 形态：无 --voice、
    --cloud-voice 提供语音证据、--voice-mode 注入 default "cloud"（M14-70
    slice 4 起 argparse 总会注入这两个属性，SimpleNamespace 显式携带）。"""
    return cli_module._run_provider_smoke_aggregate(
        SimpleNamespace(
            search=str(search),
            cloud_voice=None if cloud_voice is None else str(cloud_voice),
            llm=str(llm),
            output=str(output),
            as_json=as_json,
            voice=None if voice is None else str(voice),
            voice_mode=voice_mode,
        )
    )


def _step_payload(step: str, *, result: str = "pass") -> dict:
    """本工具导出的单步证据完整形态（聚合合法输入 fixture）。"""
    return {
        "tool": TOOL_ID,
        "schema_version": pse.SCHEMA_VERSION,
        "step": step,
        "executed": True,
        "result": result,
        "exit_code": 0 if result == "pass" else 3,
        "started_at": "2026-09-06T00:00:00+00:00",
        "completed_at": "2026-09-06T00:00:02+00:00",
        "duration_ms": 2000,
    }


def _write_step_evidence(
    directory: Path,
    provider: str,
    *,
    result: str = "pass",
    mutate=None,
) -> Path:
    """写一份单步证据文件（默认合法；mutate(payload) 可构造畸形形态）。"""
    spec = PROVIDERS[provider]
    payload = _step_payload(spec.step, result=result)
    if mutate is not None:
        mutate(payload)
    target = directory / STEP_OUTPUT_FILES[spec.step]
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def _write_three_steps(
    directory: Path, results=("pass", "pass", "pass")
) -> dict[str, Path]:
    """写齐三份合法单步证据，返回 provider -> 路径（聚合输入用）。

    M14-70 slice 2：只写聚合槽位（AGGREGATE_PROVIDERS）三 provider——
    local-voice 是 evidence track，不进聚合输入。"""
    providers = sorted(AGGREGATE_PROVIDERS)
    outcomes = dict(zip(providers, results, strict=True))
    return {
        provider: _write_step_evidence(directory, provider, result=outcomes[provider])
        for provider in providers
    }


def _write_topology_steps(
    directory: Path,
    voice: str = "cloud-voice",
    results=("pass", "pass", "pass"),
) -> dict[str, Path]:
    """按语音拓扑写齐三份合法单步证据（M14-70 slice 3）：所选语音轨道 +
    search + llm。results 槽序为 (voice, search, llm)。"""
    providers = (voice, "search", "llm")
    outcomes = dict(zip(providers, results, strict=True))
    return {
        provider: _write_step_evidence(directory, provider, result=outcomes[provider])
        for provider in providers
    }


def _poison_env(monkeypatch) -> None:
    """毒化环境：模块若读取任何敏感槽位，marker 就会泄进输出。"""
    monkeypatch.setenv("ASR_CLOUD_ENDPOINT", ENDPOINT_MARKER)
    monkeypatch.setenv("ASR_CLOUD_API_KEY", KEY_MARKER)
    monkeypatch.setenv("LLM_MODEL", MODEL_MARKER)
    monkeypatch.setenv("SEARCH_SMOKE_QUERY", QUERY_MARKER)
    monkeypatch.setenv("ASR_SMOKE_AUDIO", AUDIO_MARKER)


def _rehearsal_step(report: dict, step_id: str) -> dict:
    return next(item for item in report["steps"] if item["step"] == step_id)


def _readiness_gate(report: dict, gate_id: str) -> dict:
    return next(gate for gate in report["gates"] if gate["gate"] == gate_id)


# --- 1. CLI 注册与分发 ----------------------------------------------------------


def test_both_subcommands_registered_in_main_help(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["cli", "--help"])
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "provider-smoke-export" in out
    assert "provider-smoke-aggregate" in out


@pytest.mark.parametrize(
    "argv",
    [
        ["cli", "provider-smoke-export"],  # 缺 --output
        ["cli", "provider-smoke-aggregate"],  # 缺三个输入与 --output
        ["cli", "provider-smoke-aggregate", "--search", "x"],  # 缺 --cloud-voice/--llm/--output
    ],
)
def test_cli_missing_required_args_exit_2(monkeypatch, argv) -> None:
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2


def test_cli_invalid_provider_choice_exit_2(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "provider-smoke-export", "docker", "--output", "artifacts/x.json"],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2


def test_cli_export_dispatched_via_main(
    monkeypatch, capsys, tmp_path, fake_runner
) -> None:
    install, _calls = fake_runner
    install(0)
    output = tmp_path / "artifacts" / "search-smoke.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "provider-smoke-export", "search", "--output", str(output)],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 0
    assert output.exists()
    assert "RESULT: PASS" in capsys.readouterr().out


def test_cli_source_has_no_yes_flag() -> None:
    """parser 注册区（p_ps/p_pa 块）不得有 --yes 旗标：导出器编排运维已
    决定执行的冒烟，没有「额外授权执行」形态。"""
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    export_block = source.split("p_ps = sub.add_parser(")[1].split(
        "p_pa = sub.add_parser("
    )[0]
    aggregate_block = source.split("p_pa = sub.add_parser(")[1].split(
        "p_ep = sub.add_parser("
    )[0]
    assert '"--yes"' not in export_block
    assert '"--yes"' not in aggregate_block


# --- 2. 映射稳定性（与消费方契约交叉锁定） ---------------------------------------


def test_provider_mapping_matches_rehearsal_and_readiness() -> None:
    step_files = {spec.step_id: spec.evidence_file for spec in STEPS}
    gate = next(g for g in GATES if g.gate_id == "provider-smoke")
    assert sorted(PROVIDERS) == ["cloud-voice", "llm", "local-voice", "search"]
    # rehearsal/聚合接入面仍是三 provider（local-voice 是 M14-70 evidence
    # track，尚未接入 rehearsal STEPS 与聚合槽位）
    assert sorted(AGGREGATE_PROVIDERS) == ["cloud-voice", "llm", "search"]
    for name in AGGREGATE_PROVIDERS:
        spec = PROVIDERS[name]
        # step 与 rehearsal 精确步 id 对应，证据文件名与该步完全一致
        assert spec.step in step_files
        assert STEP_OUTPUT_FILES[spec.step] == step_files[spec.step]
        assert STEP_OUTPUT_FILES[spec.step] == f"{spec.step}.json"
        # 脚本以仓库根相对 POSIX 路径存在（cwd=仓库根调用的前提）
        assert (pse._REPOSITORY_ROOT / spec.script).is_file()
    # 聚合 providers 键 = release-readiness SMOKE_PROVIDERS 同集合同键序
    assert pse.GATE_PROVIDER_KEYS == SMOKE_PROVIDERS
    assert {spec.gate_key for spec in PROVIDERS.values()} == set(SMOKE_PROVIDERS)
    # 聚合输出文件名与 provider-smoke 门精确证据文件名一致
    assert gate.evidence_file == GATE_OUTPUT_FILE == "provider-smoke.json"


def test_script_paths_are_repo_relative_posix() -> None:
    """脚本路径必须是仓库内相对 POSIX 路径（Windows 绝对路径在 WSL bash 下
    不可解析——M11-16 设计约束）。"""
    for spec in PROVIDERS.values():
        assert not os.path.isabs(spec.script)
        assert "\\" not in spec.script
        assert spec.script.startswith("infra/smoke_")


def test_local_voice_registry_entry() -> None:
    """M14-70 slice 2：local-voice evidence track 注册条目精确锁定——
    provider/step/脚本/gate_key 四字段、单步证据文件名、脚本真实存在于
    仓库根且为相对 POSIX 路径；cloud-voice 原条目一字不动；local-voice
    不在 rehearsal STEPS 也不在聚合槽位（拓扑聚合由后续 slice 扩展）。"""
    assert PROVIDERS["local-voice"] == pse.ProviderSpec(
        "local-voice", "infra/smoke_voice_local.sh", "local-voice-smoke", "voice"
    )
    assert STEP_OUTPUT_FILES["local-voice-smoke"] == "local-voice-smoke.json"
    script = PROVIDERS["local-voice"].script
    assert not os.path.isabs(script) and "\\" not in script
    assert (pse._REPOSITORY_ROOT / script).is_file()
    assert PROVIDERS["cloud-voice"] == pse.ProviderSpec(
        "cloud-voice", "infra/smoke_voice_cloud.sh", "cloud-voice-smoke", "voice"
    )
    assert "local-voice-smoke" not in {spec.step_id for spec in STEPS}
    assert "local-voice" not in AGGREGATE_PROVIDERS


def test_local_voice_export_metadata_and_script_mapping(
    tmp_path, capsys, fake_runner
) -> None:
    """M14-70 slice 2：local-voice 单步导出（handler 直调 + fake runner，
    零真实子进程）——证据元数据与既有三 provider 同一白名单形态
    （step=local-voice-smoke、九键 STEP_EVIDENCE_KEYS），runner 收到的
    恰为 [bash, infra/smoke_voice_local.sh]，输出文件名护栏按
    STEP_OUTPUT_FILES 精确匹配。"""
    install, calls = fake_runner
    install(0)
    output = _artifacts(tmp_path) / "local-voice-smoke.json"
    assert _export_cli("local-voice", output) == 0
    assert calls == [["/fake/bin/bash", "infra/smoke_voice_local.sh"]]
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["tool"] == TOOL_ID
    assert evidence["schema_version"] == pse.SCHEMA_VERSION
    assert evidence["step"] == "local-voice-smoke"
    assert evidence["executed"] is True
    assert evidence["result"] == "pass"
    assert evidence["exit_code"] == 0
    assert set(evidence) == set(STEP_EVIDENCE_KEYS)
    assert "RESULT: PASS" in capsys.readouterr().out


def test_local_voice_build_step_evidence_fail_shape() -> None:
    """函数层直调（CLI choices 未开放前的导出面）：非零退出码 => fail
    证据照常组装（exit 1），与既有 provider 失败语义一致。"""
    evidence, exit_code = build_step_evidence(
        "local-voice",
        Path("artifacts") / "local-voice-smoke.json",
        bash="/fake/bash",
        runner=lambda argv: subprocess.CompletedProcess(argv, 3),
    )
    assert exit_code == 1
    assert evidence["step"] == "local-voice-smoke"
    assert evidence["result"] == "fail"
    assert evidence["exit_code"] == 3


def test_local_voice_rejected_by_aggregate_slots(tmp_path) -> None:
    """聚合槽位边界：默认 cloud 拓扑下 local-voice 单步证据是多余槽位 =>
    fail-closed ProviderSmokeInputError（local 轨道聚合必须显式
    voice_mode="local"——见 7b 拓扑测试组）。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    local = _write_step_evidence(artifacts, "local-voice")
    with pytest.raises(ProviderSmokeInputError):
        build_provider_smoke_evidence(
            {**steps, "local-voice": local}, artifacts / GATE_OUTPUT_FILE
        )


# --- 3. 单步导出语义（fake runner） ----------------------------------------------


def test_export_pass_exit_0_and_evidence_written(
    tmp_path, capsys, fake_runner
) -> None:
    install, _calls = fake_runner
    install(0)
    output = _artifacts(tmp_path) / "llm-smoke.json"
    assert _export_cli("llm", output) == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["step"] == "llm-smoke"
    assert evidence["executed"] is True
    assert evidence["result"] == "pass"
    assert evidence["exit_code"] == 0
    assert "RESULT: PASS" in capsys.readouterr().out


def test_export_fail_exit_1_evidence_still_written(
    tmp_path, capsys, fake_runner
) -> None:
    """非零退出码 => fail/exit 1：失败证据照常落盘（如实记录，不伪装 pass）。"""
    install, _calls = fake_runner
    install(7)
    output = _artifacts(tmp_path) / "cloud-voice-smoke.json"
    assert _export_cli("cloud-voice", output) == 1
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["result"] == "fail"
    assert evidence["exit_code"] == 7
    assert evidence["executed"] is True
    assert "RESULT: FAIL" in capsys.readouterr().out


def test_export_runner_oserror_exit_2_no_evidence(
    tmp_path, capsys, monkeypatch
) -> None:
    """runner 无法启动（OSError）=> exit 2：编排环境问题不是冒烟结论，
    不写证据、不打印结论。"""

    def boom(argv):
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr(pse, "_run_smoke_script", boom)
    output = _artifacts(tmp_path) / "search-smoke.json"
    assert _export_cli("search", output) == 2
    assert not output.exists()
    assert "RESULT" not in capsys.readouterr().out


def test_export_bash_missing_exit_2_no_execution(
    tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.setattr(pse.shutil, "which", lambda name: None)
    output = _artifacts(tmp_path) / "search-smoke.json"
    assert _export_cli("search", output) == 2
    assert not output.exists()
    assert "bash" in capsys.readouterr().out


def test_export_script_missing_exit_2_no_execution(
    tmp_path, capsys, fake_runner, monkeypatch
) -> None:
    """脚本文件不存在 => exit 2（编排环境问题），且先于 runner——不运行。"""
    install, calls = fake_runner
    install(0)
    broken = dict(PROVIDERS)
    broken["search"] = pse.ProviderSpec(
        "search", "infra/no_such_script.sh", "search-smoke", "search"
    )
    monkeypatch.setattr(pse, "PROVIDERS", broken)
    output = _artifacts(tmp_path) / "search-smoke.json"
    assert _export_cli("search", output) == 2
    assert not output.exists()
    assert calls == [], "脚本缺失时不得运行任何冒烟"
    assert "脚本不存在" in capsys.readouterr().out


def test_build_step_evidence_unknown_provider_rejected(tmp_path) -> None:
    with pytest.raises(ProviderSmokeInputError):
        build_step_evidence(
            "docker",
            tmp_path / "artifacts" / "search-smoke.json",
            bash="/fake/bash",
            runner=lambda argv: subprocess.CompletedProcess(argv, 0),
        )


def test_export_runner_receives_bash_and_script_list(
    tmp_path, fake_runner
) -> None:
    """runner 收到的恰为 [bash, 脚本相对路径] 列表参数——cwd=仓库根由
    _run_smoke_script 负责（见 runner 形态测试）。"""
    install, calls = fake_runner
    install(0)
    assert _export_cli("cloud-voice", _artifacts(tmp_path) / "cloud-voice-smoke.json") == 0
    assert calls == [["/fake/bin/bash", "infra/smoke_voice_cloud.sh"]]


# --- 4. runner 调用形态（真实 _run_smoke_script + python -c 探针） -----------------


def test_real_runner_cwd_repo_root_env_inherit_check_false(
    tmp_path, monkeypatch
) -> None:
    """真实 runner 直测（python -c 探针，无 bash 依赖、零网络）：cwd=仓库根、
    子进程整体继承当前环境、check=False（非零退出码透传）、不捕获输出。"""
    monkeypatch.setenv("PSE_TEST_MARKER", "env-inherited-7f3a")
    probe = tmp_path / "probe.json"
    code = (
        "import json, os, sys\n"
        "json.dump({'cwd': os.getcwd(),"
        " 'marker': os.environ.get('PSE_TEST_MARKER')},"
        f" open({str(probe)!r}, 'w'))\n"
        "sys.exit(7)\n"
    )
    proc = pse._run_smoke_script([sys.executable, "-c", code])
    assert isinstance(proc, subprocess.CompletedProcess)
    assert proc.returncode == 7, "check=False：非零退出码透传不抛异常"
    assert proc.stdout is None and proc.stderr is None, "不得捕获子进程输出"
    payload = json.loads(probe.read_text(encoding="utf-8"))
    assert Path(payload["cwd"]).resolve() == pse._REPOSITORY_ROOT
    assert payload["marker"] == "env-inherited-7f3a", "子进程整体继承当前环境"


# --- 5. 路径与 symlink fail-closed ------------------------------------------------


def test_export_output_outside_artifacts_exit_2_no_execution(
    tmp_path, fake_runner
) -> None:
    install, calls = fake_runner
    install(0)
    output = tmp_path / "search-smoke.json"  # 父目录名不是 artifacts/temp
    assert _export_cli("search", output) == 2
    assert not output.exists()
    assert calls == [], "路径护栏必须先于 runner（不运行冒烟）"


@pytest.mark.parametrize(
    "name",
    ["search.json", "provider-smoke.json", "search-smoke", "evidence.json"],
)
def test_export_output_filename_must_be_exact(
    tmp_path, fake_runner, name
) -> None:
    """文件名必须恰为对应步证据文件名（manifest 工具只认精确文件名）。"""
    install, calls = fake_runner
    install(0)
    assert _export_cli("search", _artifacts(tmp_path) / name) == 2
    assert calls == []
    assert not (_artifacts(tmp_path) / name).exists()


def test_export_output_existing_directory_rejected(
    tmp_path, fake_runner
) -> None:
    install, calls = fake_runner
    install(0)
    output = _artifacts(tmp_path) / "search-smoke.json"
    output.mkdir()
    assert _export_cli("search", output) == 2
    assert calls == []
    assert output.is_dir(), "目录形态不被改写"


@pytest.mark.parametrize("which", ["output", "aggregate-input", "component"])
def test_symlinks_rejected(tmp_path, fake_runner, which) -> None:
    """输出 symlink / 聚合输入 symlink / 中间目录组件 symlink 一律拒绝。"""
    install, calls = fake_runner
    install(0)
    artifacts = _artifacts(tmp_path)
    real_dir = tmp_path / "real-dir"
    real_dir.mkdir()
    real_step = _write_step_evidence(real_dir, "search")
    try:
        if which == "output":
            real = artifacts / "real.json"
            real.write_text("{}", encoding="utf-8")
            link = artifacts / "search-smoke.json"
            os.symlink(real, link)
            assert _export_cli("search", link) == 2
            assert real.read_text(encoding="utf-8") == "{}", "目标不得被写穿"
        elif which == "aggregate-input":
            # 聚合输入文件名不受步文件名约束，链接名避开三份真证据即可
            link = artifacts / "link-input.json"
            os.symlink(real_step, link)
            steps = _write_three_steps(artifacts)
            steps["search"] = link
            assert (
                _aggregate_cli(
                    steps["search"],
                    steps["cloud-voice"],
                    steps["llm"],
                    artifacts / GATE_OUTPUT_FILE,
                )
                == 2
            )
            assert not (artifacts / GATE_OUTPUT_FILE).exists()
        else:
            link_dir = artifacts / "link-dir"
            os.symlink(real_dir, link_dir, target_is_directory=True)
            assert _export_cli("search", link_dir / "search-smoke.json") == 2
            assert real_step.read_text(encoding="utf-8"), "目标文件保持原样"
    except OSError:
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    assert calls == [], "symlink 护栏先于 runner"


# --- 6. 原子写 -------------------------------------------------------------------


def test_export_write_failure_keeps_old_file_no_summary(
    tmp_path, capsys, fake_runner, monkeypatch
) -> None:
    install, _calls = fake_runner
    install(0)
    existing = _artifacts(tmp_path) / "llm-smoke.json"
    existing.write_text('{"old": true}', encoding="utf-8")

    def boom(path, text):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cli_module, "_write_report_atomic", boom)
    assert _export_cli("llm", existing) == 2
    assert existing.read_text(encoding="utf-8") == '{"old": true}'
    assert "RESULT" not in capsys.readouterr().out, "写入失败不得打印冒烟结论"


def test_no_tmp_leftover_after_writes(tmp_path, fake_runner) -> None:
    install, _calls = fake_runner
    install(0)
    artifacts = _artifacts(tmp_path)
    assert _export_cli("search", artifacts / "search-smoke.json") == 0
    steps = _write_three_steps(artifacts)
    assert (
        _aggregate_cli(
            steps["search"], steps["cloud-voice"], steps["llm"],
            artifacts / GATE_OUTPUT_FILE,
        )
        == 0
    )
    leftovers = [p.name for p in artifacts.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], "原子写不得残留临时文件"


# --- 7. 聚合校验 -----------------------------------------------------------------


def test_aggregate_all_pass_exit_0(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    output = artifacts / GATE_OUTPUT_FILE
    assert _aggregate_cli(steps["search"], steps["cloud-voice"], steps["llm"], output) == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["gate"] == "provider-smoke"
    assert evidence["topology"] == {"voice_mode": "cloud"}
    assert evidence["providers"] == {
        "voice": {
            "executed": True,
            "result": "pass",
            "evidence_step": "cloud-voice-smoke",
        },
        "search": {
            "executed": True,
            "result": "pass",
            "evidence_step": "search-smoke",
        },
        "llm": {
            "executed": True,
            "result": "pass",
            "evidence_step": "llm-smoke",
        },
    }


def test_aggregate_one_fail_exit_1_evidence_written(tmp_path, capsys) -> None:
    """任一 fail => 聚合证据照常落盘、exit 1（如实记录）。"""
    artifacts = _artifacts(tmp_path)
    # _write_three_steps 槽序为 sorted(AGGREGATE_PROVIDERS)：cloud-voice/llm/search
    steps = _write_three_steps(artifacts, results=("pass", "fail", "pass"))
    output = artifacts / GATE_OUTPUT_FILE
    assert _aggregate_cli(steps["search"], steps["cloud-voice"], steps["llm"], output) == 1
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["providers"]["llm"]["result"] == "fail"
    assert evidence["providers"]["voice"]["result"] == "pass"
    assert evidence["providers"]["search"]["result"] == "pass"
    assert "RESULT: FAIL" in capsys.readouterr().out


def test_aggregate_contract_is_minimal(tmp_path) -> None:
    """聚合契约精确锁定：providers 键序与 SMOKE_PROVIDERS 一致、每项仅
    executed/result/evidence_step、顶层仅追加 topology 自声明；不得透传
    单步 exit_code/时间/脚本细节。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts, results=("fail", "pass", "pass"))
    output = artifacts / GATE_OUTPUT_FILE
    assert _aggregate_cli(steps["search"], steps["cloud-voice"], steps["llm"], output) == 1
    file_text = output.read_text(encoding="utf-8")
    evidence = json.loads(file_text)
    assert set(evidence) == {
        "tool",
        "schema_version",
        "gate",
        "topology",
        "providers",
        "generated_at",
    }
    assert tuple(evidence["providers"]) == pse.GATE_PROVIDER_KEYS
    for entry in evidence["providers"].values():
        assert set(entry) == {"executed", "result", "evidence_step"}
    for leaked in ("exit_code", "started_at", "completed_at", "duration_ms",
                   "script", "smoke_"):
        assert leaked not in file_text, f"聚合输出不得透传单步细节 {leaked}"
    summary = format_aggregate_summary(evidence)
    for leaked in ("exit_code", "started_at", "duration_ms"):
        assert leaked not in summary


@pytest.mark.parametrize(
    ("provider", "mutate"),
    [
        # 手工拼装：tool 不是本工具
        ("search", lambda p: p.update(tool="hand-made")),
        # schema_version 与本工具不一致
        ("search", lambda p: p.update(schema_version="provider-smoke-evidence-v0")),
        # 槽位错位：search 槽位放 llm 的证据
        ("search", lambda p: p.update(step="llm-smoke")),
        # executed 非 true（not_executed 人工形态）
        ("cloud-voice", lambda p: p.update(executed=False, result="not_executed")),
        # result 只能 pass/fail：not_executed 即便 executed=true 也拒绝
        ("llm", lambda p: p.update(result="not_executed")),
        ("llm", lambda p: p.update(result="unknown")),
        ("llm", lambda p: p.update(result=None)),
    ],
)
def test_aggregate_rejects_non_tool_shapes(tmp_path, capsys, provider, mutate) -> None:
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    steps[provider] = _write_step_evidence(artifacts, provider, mutate=mutate)
    output = artifacts / GATE_OUTPUT_FILE
    assert (
        _aggregate_cli(steps["search"], steps["cloud-voice"], steps["llm"], output) == 2
    )
    assert not output.exists()
    assert "拒绝执行" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("provider", "mutate"),
    [
        # 缺任一字段（含四个 metadata 字段）——键集合不完整即拒绝
        ("search", lambda p: p.pop("tool")),
        ("search", lambda p: p.pop("schema_version")),
        ("search", lambda p: p.pop("step")),
        ("search", lambda p: p.pop("executed")),
        ("search", lambda p: p.pop("result")),
        ("search", lambda p: p.pop("exit_code")),
        ("search", lambda p: p.pop("started_at")),
        ("search", lambda p: p.pop("completed_at")),
        ("search", lambda p: p.pop("duration_ms")),
        # 多一个额外字段
        ("llm", lambda p: p.update(extra_field="x")),
        ("llm", lambda p: p.update(note="hand-made annotation")),
        # 结论与退出码矛盾：pass 但 exit_code 非 0 / fail 但 exit_code=0
        ("search", lambda p: p.update(exit_code=1)),
        ("llm", lambda p: p.update(result="fail", exit_code=0)),
        # 无效 / 非字符串 / naive 时间
        ("search", lambda p: p.update(started_at="not-a-time")),
        ("search", lambda p: p.update(completed_at=12345)),
        ("cloud-voice", lambda p: p.update(started_at="2026-09-06T00:00:00")),
        ("cloud-voice", lambda p: p.update(completed_at="2026-09-06T00:00:02")),
        # completed 早于 started（时间倒置）
        (
            "llm",
            lambda p: p.update(
                started_at="2026-09-06T00:00:05+00:00",
                completed_at="2026-09-06T00:00:01+00:00",
            ),
        ),
        # 负 duration
        ("search", lambda p: p.update(duration_ms=-1)),
        # bool 伪装 int（bool 是 int 子类，必须显式拒绝）
        ("llm", lambda p: p.update(exit_code=True)),
        ("llm", lambda p: p.update(duration_ms=True)),
        # 非 int 形态
        ("llm", lambda p: p.update(duration_ms="2000")),
        ("llm", lambda p: p.update(exit_code=1.5)),
    ],
)
def test_aggregate_rejects_metadata_schema_violations(
    tmp_path, capsys, provider, mutate
) -> None:
    """聚合输入 exact schema 回归（Codex 验收整改）：缺任一字段/多额外字段/
    结论与退出码矛盾/无效或 naive 或非字符串时间/时间倒置/负 duration/
    bool 伪装 int——一律 fail-closed exit 2、不写聚合输出、输入字节不变。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    mutated = _write_step_evidence(artifacts, provider, mutate=mutate)
    steps[provider] = mutated
    before = mutated.read_bytes()
    output = artifacts / GATE_OUTPUT_FILE
    assert (
        _aggregate_cli(steps["search"], steps["cloud-voice"], steps["llm"], output) == 2
    )
    assert not output.exists()
    assert mutated.read_bytes() == before, "输入字节必须保持不变"
    assert "RESULT" not in capsys.readouterr().out


def test_aggregate_accepts_equal_timestamp_zero_duration(tmp_path) -> None:
    """等时零耗时（起止时间相同、duration_ms=0）是真实导出可产生的形态
    ——exact schema 校验按 completed_at >= started_at 放行，不得误拒。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    _write_step_evidence(
        artifacts,
        "search",
        mutate=lambda p: p.update(
            started_at="2026-09-06T00:00:00+00:00",
            completed_at="2026-09-06T00:00:00+00:00",
            duration_ms=0,
        ),
    )
    output = artifacts / GATE_OUTPUT_FILE
    assert (
        _aggregate_cli(steps["search"], steps["cloud-voice"], steps["llm"], output) == 0
    )
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["providers"]["search"]["result"] == "pass"


@pytest.mark.parametrize(
    "raw",
    [
        "not json {",
        "\udc80 Invalid UTF-8 bytes",
        '["not", "an", "object"]',
        "null",
    ],
)
def test_aggregate_rejects_malformed_files(tmp_path, raw) -> None:
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    steps["llm"].write_text(raw, encoding="utf-8", errors="surrogateescape")
    output = artifacts / GATE_OUTPUT_FILE
    assert (
        _aggregate_cli(steps["search"], steps["cloud-voice"], steps["llm"], output) == 2
    )
    assert not output.exists()


@pytest.mark.parametrize("which", ["search", "cloud-voice", "llm"])
def test_aggregate_input_path_guards(tmp_path, which) -> None:
    """聚合输入越界（非 artifacts/temp）/ 不存在 / 是目录 => 拒绝。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    steps[which] = tmp_path / "outside.json"  # 父目录名不是 artifacts/temp
    output = artifacts / GATE_OUTPUT_FILE
    assert (
        _aggregate_cli(steps["search"], steps["cloud-voice"], steps["llm"], output) == 2
    )
    steps[which] = artifacts / "missing.json"
    assert (
        _aggregate_cli(steps["search"], steps["cloud-voice"], steps["llm"], output) == 2
    )
    directory = artifacts / "a-directory"
    directory.mkdir()
    steps[which] = directory
    assert (
        _aggregate_cli(steps["search"], steps["cloud-voice"], steps["llm"], output) == 2
    )
    assert not output.exists()


@pytest.mark.parametrize("variant", ["direct", "dotdot"])
def test_aggregate_output_overlapping_input_rejected(
    tmp_path, capsys, variant
) -> None:
    """输出不得等于任何输入：等价路径书写形态（.. 折叠）不构成绕过；
    冲突时输入字节不变。大小写变体由输出文件名精确检查先行拦截（见
    filename 测试），到不了重叠分支。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    # 把 search 证据复制到 provider-smoke.json 名下并作 search 槽位输入
    # （门输出名恰被槽位占用，构造 output == input 冲突）
    gate_input = artifacts / GATE_OUTPUT_FILE
    gate_input.write_text(
        steps["search"].read_text(encoding="utf-8"), encoding="utf-8"
    )
    if variant == "direct":
        conflict = gate_input
    else:
        conflict = os.path.join(
            str(artifacts), "..", artifacts.name, GATE_OUTPUT_FILE
        )
    before = gate_input.read_bytes()
    assert (
        _aggregate_cli(gate_input, steps["cloud-voice"], steps["llm"], conflict) == 2
    )
    assert "拒绝覆盖输入" in capsys.readouterr().out
    assert gate_input.read_bytes() == before, "输入字节必须保持不变"


@pytest.mark.parametrize("variant", ["direct", "equivalent"])
def test_aggregate_duplicate_input_rejected(tmp_path, capsys, variant) -> None:
    """同一输入文件不得重复传入两个槽位（一份证据不得虚增覆盖面）；
    等价路径书写形态（.. 折叠 / Windows 大小写变体）不构成绕过。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    if variant == "direct":
        duplicate = str(steps["search"])
    elif os.path.normcase("A") == os.path.normcase("a"):
        duplicate = os.path.join(
            str(artifacts), steps["search"].name.swapcase()
        )
    else:
        duplicate = os.path.join(
            str(artifacts), "..", artifacts.name, steps["search"].name
        )
    output = artifacts / GATE_OUTPUT_FILE
    # search 与 llm 两个槽位传入同一份文件（重复检查先于内容读取）
    assert (
        _aggregate_cli(duplicate, steps["cloud-voice"], duplicate, output) == 2
    )
    captured = capsys.readouterr()
    assert "同一份" in captured.out, "重复输入不得虚增 provider 覆盖面"
    assert "RESULT" not in captured.out
    assert not output.exists()


@pytest.mark.parametrize(
    "name", ["gate.json", "provider-smoke", "PROVIDER-SMOKE.JSON"]
)
def test_aggregate_output_filename_must_be_exact(tmp_path, capsys, name) -> None:
    """输出文件名必须恰为 provider-smoke.json（大小写变体同样拒绝——
    manifest 工具只认精确文件名）。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    assert (
        _aggregate_cli(
            steps["search"], steps["cloud-voice"], steps["llm"], artifacts / name
        )
        == 2
    )
    assert "provider-smoke.json" in capsys.readouterr().out
    assert not (artifacts / GATE_OUTPUT_FILE).exists(), "门输出不得落盘"


def test_aggregate_requires_exact_three_slots(tmp_path) -> None:
    """槽位不齐（缺/多）=> ProviderSmokeInputError（fail-closed）。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    output = artifacts / GATE_OUTPUT_FILE
    with pytest.raises(ProviderSmokeInputError):
        build_provider_smoke_evidence(
            {k: v for k, v in steps.items() if k != "llm"}, output
        )
    with pytest.raises(ProviderSmokeInputError):
        build_provider_smoke_evidence({**steps, "extra": steps["llm"]}, output)


# --- 7b. 拓扑感知聚合（M14-70 slice 3；纯函数直调，CLI voice_mode 旗标由
# --- 后续 slice 接入，默认 "cloud" 保证既有 CLI 行为不变） ------------------------


def test_aggregate_default_cloud_matches_explicit_cloud(tmp_path) -> None:
    """默认 voice_mode="cloud" 向后兼容：不传与显式 "cloud" 输出除
    generated_at 外逐字段一致；topology 如实自声明 voice_mode=cloud。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    output = artifacts / GATE_OUTPUT_FILE
    implicit, implicit_code = build_provider_smoke_evidence(steps, output)
    explicit, explicit_code = build_provider_smoke_evidence(
        steps, output, voice_mode="cloud"
    )
    assert implicit_code == explicit_code == 0
    implicit.pop("generated_at")
    explicit.pop("generated_at")
    assert explicit == implicit
    assert implicit["topology"] == {"voice_mode": "cloud"}


def test_aggregate_local_topology_selects_local_voice(tmp_path) -> None:
    """local 拓扑：输入槽位恰为 local-voice/search/llm；输出
    topology.voice_mode=local、voice 槽位 evidence_step=local-voice-smoke
    （cloud-voice 不参与）；任一 fail => exit 1 语义保持。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_topology_steps(artifacts, "local-voice")
    evidence, code = build_provider_smoke_evidence(
        steps, artifacts / GATE_OUTPUT_FILE, voice_mode="local"
    )
    assert code == 0
    assert evidence["topology"] == {"voice_mode": "local"}
    assert tuple(evidence["providers"]) == pse.GATE_PROVIDER_KEYS
    assert evidence["providers"]["voice"] == {
        "executed": True,
        "result": "pass",
        "evidence_step": "local-voice-smoke",
    }
    assert evidence["providers"]["search"]["evidence_step"] == "search-smoke"
    assert evidence["providers"]["llm"]["evidence_step"] == "llm-smoke"
    # 独立子目录防文件名冲突：先建父级，_artifacts 只在其下拼 artifacts
    failing_parent = tmp_path / "failing"
    failing_parent.mkdir()
    failing_dir = _artifacts(failing_parent)
    failing = _write_topology_steps(
        failing_dir, "local-voice", results=("fail", "pass", "pass")
    )
    failed_evidence, failed_code = build_provider_smoke_evidence(
        failing, failing_dir / GATE_OUTPUT_FILE, voice_mode="local"
    )
    assert failed_code == 1
    assert failed_evidence["providers"]["voice"]["result"] == "fail"
    assert failed_evidence["providers"]["voice"]["evidence_step"] == (
        "local-voice-smoke"
    )


@pytest.mark.parametrize("voice_mode", ["hybrid", "cloud"])
def test_aggregate_hybrid_and_cloud_map_to_cloud_voice(
    tmp_path, voice_mode
) -> None:
    """hybrid/cloud 恒选 cloud-voice 证据轨道：槽位仍为
    cloud-voice/search/llm（local-voice 是多余槽位）、voice 槽位
    evidence_step=cloud-voice-smoke、topology 如实自声明。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    output = artifacts / GATE_OUTPUT_FILE
    evidence, code = build_provider_smoke_evidence(
        steps, output, voice_mode=voice_mode
    )
    assert code == 0
    assert evidence["topology"] == {"voice_mode": voice_mode}
    assert evidence["providers"]["voice"]["evidence_step"] == "cloud-voice-smoke"
    local = _write_step_evidence(artifacts, "local-voice")
    with pytest.raises(ProviderSmokeInputError):
        build_provider_smoke_evidence(
            {**steps, "local-voice": local}, output, voice_mode=voice_mode
        )


def test_aggregate_invalid_voice_mode_rejected_before_inputs(tmp_path) -> None:
    """非法 voice_mode 先于一切输入处理：槽位集合法也拒绝且错误指向
    voice_mode；空输入同样报 mode 错误（证明先于槽位检查）；枚举精确
    锁定为 ("local", "hybrid", "cloud")。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    output = artifacts / GATE_OUTPUT_FILE
    for bad_mode in ("edge", "Cloud", "local/cloud", "", None, 1):
        with pytest.raises(ProviderSmokeInputError, match="voice_mode"):
            build_provider_smoke_evidence(steps, output, voice_mode=bad_mode)
    with pytest.raises(ProviderSmokeInputError, match="voice_mode"):
        build_provider_smoke_evidence({}, output, voice_mode="edge")
    assert VOICE_MODES == ("local", "hybrid", "cloud")
    assert pse.VOICE_MODES == VOICE_MODES


@pytest.mark.parametrize("voice_mode", ["cloud", "hybrid"])
def test_aggregate_rejects_local_evidence_in_cloud_slot(
    tmp_path, voice_mode
) -> None:
    """cloud/hybrid 拓扑的语音槽位传入 local-voice 证据文件（内容 step
    与槽位 provider 错位）=> fail-closed 槽位错位拒绝。"""
    artifacts = _artifacts(tmp_path)
    masquerade = _write_step_evidence(artifacts, "local-voice")
    with pytest.raises(ProviderSmokeInputError, match="槽位错位"):
        build_provider_smoke_evidence(
            {
                "cloud-voice": masquerade,
                "search": _write_step_evidence(artifacts, "search"),
                "llm": _write_step_evidence(artifacts, "llm"),
            },
            artifacts / GATE_OUTPUT_FILE,
            voice_mode=voice_mode,
        )


def test_aggregate_rejects_cloud_evidence_in_local_slot(tmp_path) -> None:
    """local 拓扑的 local-voice 槽位传入 cloud-voice 证据文件 => 槽位
    错位拒绝。"""
    artifacts = _artifacts(tmp_path)
    masquerade = _write_step_evidence(artifacts, "cloud-voice")
    with pytest.raises(ProviderSmokeInputError, match="槽位错位"):
        build_provider_smoke_evidence(
            {
                "local-voice": masquerade,
                "search": _write_step_evidence(artifacts, "search"),
                "llm": _write_step_evidence(artifacts, "llm"),
            },
            artifacts / GATE_OUTPUT_FILE,
            voice_mode="local",
        )


def test_aggregate_local_mode_requires_local_voice_slot(tmp_path) -> None:
    """local 拓扑槽位集精确：只给 cloud-voice/search/llm（缺 local-voice
    且多 cloud-voice）=> 槽位集拒绝。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    with pytest.raises(ProviderSmokeInputError, match="local-voice"):
        build_provider_smoke_evidence(
            steps, artifacts / GATE_OUTPUT_FILE, voice_mode="local"
        )


def test_aggregate_inputs_unchanged_after_runs(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts, results=("pass", "fail", "pass"))
    before = {name: path.read_bytes() for name, path in steps.items()}
    output = artifacts / GATE_OUTPUT_FILE
    assert (
        _aggregate_cli(steps["search"], steps["cloud-voice"], steps["llm"], output) == 1
    )
    assert (
        _aggregate_cli(steps["search"], steps["cloud-voice"], steps["llm"], output)
        == 1
    )
    assert {name: path.read_bytes() for name, path in steps.items()} == before


# --- 7c. CLI 语音拓扑旗标（M14-70 slice 4：--voice/--voice-mode 经真实
# --- argparse 注册与 main() 分发；legacy --cloud-voice 兼容形态保持不变） -----------


def test_aggregate_legacy_no_mode_dispatch_unchanged(
    tmp_path, capsys, monkeypatch
) -> None:
    """legacy 无旗标形态经 main() 真实分发：只给 --cloud-voice（不带
    --voice-mode）=> argparse 注入 voice_mode 默认 cloud、聚合成功，
    topology 如实自声明 cloud、voice 槽位消费 cloud-voice-smoke 证据
    （既有调用零改动）。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    output = artifacts / GATE_OUTPUT_FILE
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "provider-smoke-aggregate",
            "--search",
            str(steps["search"]),
            "--cloud-voice",
            str(steps["cloud-voice"]),
            "--llm",
            str(steps["llm"]),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["topology"] == {"voice_mode": "cloud"}
    assert evidence["providers"]["voice"]["evidence_step"] == "cloud-voice-smoke"
    assert "RESULT: PASS" in capsys.readouterr().out


@pytest.mark.parametrize("voice_mode", ["local", "hybrid", "cloud"])
def test_aggregate_voice_flag_modes_via_main(
    tmp_path, capsys, monkeypatch, voice_mode
) -> None:
    """新形态 --voice + --voice-mode 经 main() 真实分发：local 消费
    local-voice-smoke 证据、hybrid/cloud 消费 cloud-voice-smoke 证据，
    topology 如实自声明所选 mode。"""
    artifacts = _artifacts(tmp_path)
    voice_track = "local-voice" if voice_mode == "local" else "cloud-voice"
    steps = _write_topology_steps(artifacts, voice_track)
    output = artifacts / GATE_OUTPUT_FILE
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "provider-smoke-aggregate",
            "--search",
            str(steps["search"]),
            "--voice",
            str(steps[voice_track]),
            "--llm",
            str(steps["llm"]),
            "--voice-mode",
            voice_mode,
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["topology"] == {"voice_mode": voice_mode}
    assert evidence["providers"]["voice"]["evidence_step"] == (
        f"{voice_track}-smoke"
    )
    assert "RESULT: PASS" in capsys.readouterr().out


def test_aggregate_hybrid_accepts_legacy_cloud_voice_flag(
    tmp_path, monkeypatch
) -> None:
    """--voice-mode hybrid 接受 legacy --cloud-voice 语音输入：映射到
    cloud-voice 槽位聚合成功（hybrid 拓扑不强制改用 --voice）。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    output = artifacts / GATE_OUTPUT_FILE
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "provider-smoke-aggregate",
            "--search",
            str(steps["search"]),
            "--cloud-voice",
            str(steps["cloud-voice"]),
            "--llm",
            str(steps["llm"]),
            "--voice-mode",
            "hybrid",
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["topology"] == {"voice_mode": "hybrid"}
    assert evidence["providers"]["voice"]["evidence_step"] == "cloud-voice-smoke"


def test_aggregate_cloud_voice_flag_equivalence(tmp_path) -> None:
    """cloud 拓扑下新形态 --voice 与 legacy --cloud-voice 指向同一份
    cloud-voice 证据 => 聚合输出逐字段等价（除 generated_at）。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    output = artifacts / GATE_OUTPUT_FILE
    new_code = _aggregate_cli(
        steps["search"],
        None,
        steps["llm"],
        output,
        voice=steps["cloud-voice"],
        voice_mode="cloud",
    )
    assert new_code == 0
    new_evidence = json.loads(output.read_text(encoding="utf-8"))
    legacy_parent = tmp_path / "legacy"
    legacy_parent.mkdir()
    legacy_dir = _artifacts(legacy_parent)
    legacy_steps = _write_three_steps(legacy_dir)
    legacy_code = _aggregate_cli(
        legacy_steps["search"],
        legacy_steps["cloud-voice"],
        legacy_steps["llm"],
        legacy_dir / GATE_OUTPUT_FILE,
    )
    assert legacy_code == 0
    legacy_evidence = json.loads(
        (legacy_dir / GATE_OUTPUT_FILE).read_text(encoding="utf-8")
    )
    new_evidence.pop("generated_at")
    legacy_evidence.pop("generated_at")
    assert new_evidence == legacy_evidence


def test_aggregate_both_voice_flags_rejected(tmp_path, capsys) -> None:
    """--voice 与 --cloud-voice 同给 => exit 2、不写输出、不打印结论。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    output = artifacts / GATE_OUTPUT_FILE
    assert (
        _aggregate_cli(
            steps["search"],
            steps["cloud-voice"],
            steps["llm"],
            output,
            voice=steps["cloud-voice"],
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "只能提供一个" in captured.out
    assert "RESULT" not in captured.out
    assert not output.exists()


@pytest.mark.parametrize("voice_mode", ["cloud", "hybrid", "local"])
def test_aggregate_no_voice_flag_rejected(tmp_path, capsys, monkeypatch,
                                          voice_mode) -> None:
    """语音输入缺失（--voice/--cloud-voice 都不给）=> exit 2：handler
    直调与 main() 真实分发（argparse 不再强制 --cloud-voice，缺语音输入
    退化为 handler 校验）两种形态都拒绝且不写输出。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    output = artifacts / GATE_OUTPUT_FILE
    assert (
        _aggregate_cli(
            steps["search"], None, steps["llm"], output, voice_mode=voice_mode
        )
        == 2
    )
    assert "恰好提供一个语音单步" in capsys.readouterr().out
    assert not output.exists()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "provider-smoke-aggregate",
            "--search",
            str(steps["search"]),
            "--llm",
            str(steps["llm"]),
            "--voice-mode",
            voice_mode,
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2
    assert not output.exists()


def test_aggregate_local_mode_rejects_cloud_voice_flag(
    tmp_path, capsys
) -> None:
    """--voice-mode local 只有 --cloud-voice（legacy 旗标误用）=> exit 2、
    不写输出、不打印聚合结论（local 轨道语音证据必须经 --voice 提供）。"""
    artifacts = _artifacts(tmp_path)
    local_steps = _write_topology_steps(artifacts, "local-voice")
    output = artifacts / GATE_OUTPUT_FILE
    assert (
        _aggregate_cli(
            local_steps["search"],
            local_steps["local-voice"],
            local_steps["llm"],
            output,
            voice_mode="local",
        )
        == 2
    )
    captured = capsys.readouterr()
    assert "必须经 --voice 提供" in captured.out
    assert "RESULT" not in captured.out
    assert not output.exists()


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_cli_export_choices_match_registry_via_main(
    tmp_path, monkeypatch, fake_runner, provider
) -> None:
    """export choices 与 PROVIDERS 注册一致：四个注册 provider（含
    local-voice）全部可经 main() 真实分发成功导出（输出文件名护栏按
    STEP_OUTPUT_FILES 精确匹配）。"""
    install, calls = fake_runner
    install(0)
    step = PROVIDERS[provider].step
    output = _artifacts(tmp_path) / STEP_OUTPUT_FILES[step]
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "provider-smoke-export", provider, "--output", str(output)],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 0
    assert output.exists()
    assert calls == [["/fake/bin/bash", PROVIDERS[provider].script]]


def test_cli_invalid_voice_mode_exit_2(monkeypatch, tmp_path) -> None:
    """--voice-mode 非法值被 argparse choices 枚举拒绝：exit 2 且不
    触碰任何聚合输入（证据文件从未被读取——handler 不执行）。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    output = artifacts / GATE_OUTPUT_FILE
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "provider-smoke-aggregate",
            "--search",
            str(steps["search"]),
            "--cloud-voice",
            str(steps["cloud-voice"]),
            "--llm",
            str(steps["llm"]),
            "--voice-mode",
            "edge",
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2
    assert not output.exists()


# --- 8. 输出契约与消费 ------------------------------------------------------------


@pytest.mark.parametrize("provider", sorted(AGGREGATE_PROVIDERS))
@pytest.mark.parametrize(("result", "expected"), [("pass", "pass"), ("fail", "blocked")])
def test_step_evidence_passes_rehearsal_evaluator_directly(
    fake_runner, provider, result, expected
) -> None:
    """单步证据 dict 直接过 cutover-rehearsal 三冒烟步评估器（白名单字段
    可提取；多余 tool/exit_code/时间字段不影响）。"""
    install, _calls = fake_runner
    install(0 if result == "pass" else 5)
    evidence, _code = build_step_evidence(
        provider,
        Path("artifacts") / STEP_OUTPUT_FILES[PROVIDERS[provider].step],
        bash="/fake/bash",
    )
    status, reason, _action, data, _supporting = REHEARSAL_EVALUATORS[
        PROVIDERS[provider].step
    ](evidence, None, {})
    assert status == expected
    assert data == {"executed": True, "result": result}
    assert reason


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        (("pass", "pass", "pass"), "pass"),
        (("pass", "fail", "pass"), "blocked"),
        (("fail", "fail", "fail"), "blocked"),
    ],
)
def test_aggregate_evidence_passes_readiness_evaluator_directly(
    tmp_path, results, expected
) -> None:
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts, results=results)
    evidence, _code = build_provider_smoke_evidence(
        {
            "search": steps["search"],
            "cloud-voice": steps["cloud-voice"],
            "llm": steps["llm"],
        },
        artifacts / GATE_OUTPUT_FILE,
    )
    status, _reason, data, _supporting = READINESS_EVALUATORS["provider-smoke"](
        evidence, artifacts, {}
    )
    assert status == expected
    # M14-70 聚合契约：data 为嵌套形态（topology + providers），默认聚合
    # 拓扑 cloud；results 槽序 = sorted(AGGREGATE_PROVIDERS)（cloud-voice,
    # llm, search），voice 槽位按拓扑配对 cloud-voice-smoke。
    outcomes = dict(zip(sorted(AGGREGATE_PROVIDERS), results, strict=True))
    assert set(data) == {"topology", "providers"}
    assert data["topology"] == {"voice_mode": "cloud"}
    assert data["providers"] == {
        "voice": {
            "executed": True,
            "result": outcomes["cloud-voice"],
            "evidence_step": "cloud-voice-smoke",
        },
        "search": {
            "executed": True,
            "result": outcomes["search"],
            "evidence_step": "search-smoke",
        },
        "llm": {
            "executed": True,
            "result": outcomes["llm"],
            "evidence_step": "llm-smoke",
        },
    }


def test_written_step_files_consumed_by_full_rehearsal(
    tmp_path, fake_runner
) -> None:
    """端到端：fake runner 导出的三份单步证据落盘 => 完整 run_cutover_rehearsal
    消费（pass => 该步 pass、其余步缺证据 not_executed；fail => blocked）。"""
    install, _calls = fake_runner
    artifacts = _artifacts(tmp_path)
    install(0)
    for provider in sorted(AGGREGATE_PROVIDERS):
        step = PROVIDERS[provider].step
        assert _export_cli(provider, artifacts / STEP_OUTPUT_FILES[step]) == 0
    install(4)
    assert _export_cli("llm", artifacts / "llm-smoke.json") == 1
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    for name in ("search-smoke.json", "cloud-voice-smoke.json", "llm-smoke.json"):
        (evidence_dir / name).write_text(
            (artifacts / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    rehearsal = run_cutover_rehearsal(evidence_dir)
    statuses = {
        item["step"]: item["status"]
        for item in rehearsal["steps"]
        if item["step"] in {"search-smoke", "cloud-voice-smoke", "llm-smoke"}
    }
    assert statuses == {
        "search-smoke": "pass",
        "cloud-voice-smoke": "pass",
        "llm-smoke": "blocked",
    }


def test_written_aggregate_consumed_by_full_readiness(tmp_path, fake_runner) -> None:
    """端到端：三份 fake 导出 + 聚合落盘 => 完整 run_release_readiness 消费
    provider-smoke 门 pass（其余门缺证据不阻断该门结论）。"""
    install, _calls = fake_runner
    artifacts = _artifacts(tmp_path)
    install(0)
    for provider in sorted(AGGREGATE_PROVIDERS):
        step = PROVIDERS[provider].step
        assert _export_cli(provider, artifacts / STEP_OUTPUT_FILES[step]) == 0
    steps = {
        provider: artifacts / STEP_OUTPUT_FILES[PROVIDERS[provider].step]
        for provider in sorted(AGGREGATE_PROVIDERS)
    }
    assert (
        _aggregate_cli(
            steps["search"], steps["cloud-voice"], steps["llm"],
            artifacts / GATE_OUTPUT_FILE,
        )
        == 0
    )
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    (evidence_dir / GATE_OUTPUT_FILE).write_text(
        (artifacts / GATE_OUTPUT_FILE).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    report = run_release_readiness(evidence_dir)
    gate = _readiness_gate(report, "provider-smoke")
    assert gate["status"] == "pass"
    # M14-70 聚合契约：完整 readiness 消费同一嵌套形态（默认 cloud 拓扑，
    # voice 槽位按拓扑配对 cloud-voice-smoke）
    assert gate["data"] == {
        "topology": {"voice_mode": "cloud"},
        "providers": {
            name: {
                "executed": True,
                "result": "pass",
                "evidence_step": step,
            }
            for name, step in (
                ("voice", "cloud-voice-smoke"),
                ("search", "search-smoke"),
                ("llm", "llm-smoke"),
            )
        },
    }


# --- 9. 零敏感信息落盘 ------------------------------------------------------------


def test_step_evidence_top_level_keys_exact(tmp_path, fake_runner) -> None:
    """单步证据顶层键恰为白名单：tool + schema_version/step/executed/result/
    exit_code/started_at/completed_at/duration_ms——多一个少一个都是契约漂移。
    与 STEP_EVIDENCE_KEYS（聚合 exact schema 校验依据）交叉锁定：导出面与
    校验面键集必须一致，否则聚合会拒掉自家导出或放进漂移形态。"""
    install, _calls = fake_runner
    install(0)
    output = _artifacts(tmp_path) / "search-smoke.json"
    assert _export_cli("search", output) == 0
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert set(evidence) == set(STEP_EVIDENCE_KEYS)
    assert set(STEP_EVIDENCE_KEYS) == {
        "tool",
        "schema_version",
        "step",
        "executed",
        "result",
        "exit_code",
        "started_at",
        "completed_at",
        "duration_ms",
    }


def test_no_secrets_in_any_output(tmp_path, capsys, fake_runner, monkeypatch) -> None:
    """毒化敏感环境（fake endpoint/key/模型名/查询词/音频路径）：
    stdout/stderr/摘要/落盘文件零泄漏——模块不读取 provider 端点/密钥/
    模型/查询/音频等敏感环境槽位（bash 的 PATH 解析不构成敏感槽位访问）、
    证据只含白名单标量。"""
    _poison_env(monkeypatch)
    install, _calls = fake_runner
    install(0)
    artifacts = _artifacts(tmp_path)
    output = artifacts / "llm-smoke.json"
    capsys.readouterr()
    assert _export_cli("llm", output, as_json=True) == 0
    captured = capsys.readouterr()
    file_text = output.read_text(encoding="utf-8")
    summary = format_step_summary(json.loads(file_text))
    for marker in ALL_MARKERS:
        assert marker not in captured.out
        assert marker not in captured.err
        assert marker not in file_text
        assert marker not in summary
    evidence = json.loads(file_text)
    assert find_sensitive_key(evidence) is None
    assert find_embedded_credential(evidence) is None


def test_aggregate_output_no_sensitive_surface(tmp_path) -> None:
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts, results=("pass", "fail", "pass"))
    evidence, _code = build_provider_smoke_evidence(
        steps, artifacts / GATE_OUTPUT_FILE
    )
    assert find_sensitive_key(evidence) is None
    assert find_embedded_credential(evidence) is None


def test_smoke_scripts_not_executed_by_aggregate(tmp_path) -> None:
    """聚合是纯本地文件推导：不运行任何冒烟（源码无 runner 引用，行为面
    由源码守卫测试锁定，此处锁聚合 CLI 不产生任何子进程的可观察语义——
    输入为手写合法证据，聚合前后输入字节不变且无新文件）。"""
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    before = sorted(p.name for p in artifacts.iterdir())
    assert (
        _aggregate_cli(
            steps["search"], steps["cloud-voice"], steps["llm"],
            artifacts / GATE_OUTPUT_FILE,
        )
        == 0
    )
    after = sorted(p.name for p in artifacts.iterdir())
    assert set(after) - set(before) == {GATE_OUTPUT_FILE}


# --- 10. 源码级守卫 --------------------------------------------------------------


def test_module_has_no_env_db_or_network_surface() -> None:
    """源码级证明（非行为面零环境访问声明）：模块源码零直接环境变量读取
    （零 os.environ/getenv 引用——shutil.which 经 PATH 解析 bash 是库内部
    行为，属预期的唯一环境访问，不是本模块源码的敏感槽位读取）、零 DB/
    零网络库引用（subprocess 是本工具的职责本身，不在禁止列；其调用形态
    由下一条 ast 锁定）。"""
    source = Path(pse.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_doc = ast.get_docstring(tree) or ""
    code = source.replace(module_doc, "")
    for banned in (
        "os.environ",
        "getenv",
        "httpx",
        "requests",
        "socket",
        "urllib",
        "create_engine",
        "asyncpg",
        "psycopg",
        "asyncio",
    ):
        assert banned not in code, f"不得出现 {banned}"


def test_subprocess_call_shape_is_pinned() -> None:
    """唯一的 subprocess.run 调用形态 ast 锁定：列表参数（argv 变量——非
    shell 字符串）、cwd=仓库根、check=False、无 env=（整体继承当前环境）、
    无 stdout=/stderr=/capture_output=（不捕获脚本脱敏摘要）。"""
    source = Path(pse.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    ]
    assert len(calls) == 1, "模块只允许一处 subprocess.run（真实 runner）"
    call = calls[0]
    assert len(call.args) == 1 and isinstance(call.args[0], ast.Name)
    assert call.args[0].id == "argv", "runner 必须接收列表参数（禁 shell 字符串）"
    keywords = {kw.arg: kw.value for kw in call.keywords}
    assert "shell" not in keywords
    assert "env" not in keywords, "不得构造 env=（整体继承当前环境）"
    assert "stdout" not in keywords and "stderr" not in keywords
    assert "capture_output" not in keywords, "不得捕获脚本输出"
    check = keywords.get("check")
    assert isinstance(check, ast.Constant) and check.value is False
    cwd = keywords.get("cwd")
    assert cwd is not None, "必须固定 cwd=仓库根（脚本相对路径前提）"


# --- 11. --json 模式 -------------------------------------------------------------


def test_export_json_stdout_pure_and_matches_file(tmp_path, capsys, fake_runner) -> None:
    install, _calls = fake_runner
    install(0)
    output = _artifacts(tmp_path) / "search-smoke.json"
    capsys.readouterr()
    assert _export_cli("search", output, as_json=True) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("{"), "stdout 必须是纯 JSON"
    assert "证据已写入" in captured.err, "人读提示走 stderr"
    assert json.loads(captured.out) == json.loads(output.read_text(encoding="utf-8"))


def test_aggregate_json_stdout_pure_and_matches_file(tmp_path, capsys) -> None:
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    output = artifacts / GATE_OUTPUT_FILE
    capsys.readouterr()
    assert (
        _aggregate_cli(
            steps["search"], steps["cloud-voice"], steps["llm"], output, as_json=True
        )
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out.startswith("{"), "stdout 必须是纯 JSON"
    assert "证据已写入" in captured.err
    assert json.loads(captured.out) == json.loads(output.read_text(encoding="utf-8"))


# --- 12. clock 注入与纯函数直调 ----------------------------------------------------


def test_build_step_evidence_injected_clock_and_duration() -> None:
    fixed_start = datetime(2026, 9, 6, 0, 0, 0, tzinfo=UTC)
    fixed_end = datetime(2026, 9, 6, 0, 0, 3, tzinfo=UTC)
    times = iter([fixed_start, fixed_end])
    evidence, exit_code = build_step_evidence(
        "llm",
        Path("artifacts") / "llm-smoke.json",
        bash="/fake/bash",
        runner=lambda argv: subprocess.CompletedProcess(argv, 0),
        clock=lambda: next(times),
    )
    assert exit_code == 0
    assert evidence["started_at"] == fixed_start.isoformat()
    assert evidence["completed_at"] == fixed_end.isoformat()
    assert evidence["duration_ms"] == 3000
    # 负耗时（时钟回拨形态）钳 0，不得出现负 duration
    times = iter([fixed_end, fixed_start])
    evidence, _exit = build_step_evidence(
        "llm",
        Path("artifacts") / "llm-smoke.json",
        bash="/fake/bash",
        runner=lambda argv: subprocess.CompletedProcess(argv, 1),
        clock=lambda: next(times),
    )
    assert evidence["duration_ms"] == 0
    assert evidence["result"] == "fail"


def test_format_summaries_carry_result_and_redaction_notes(tmp_path) -> None:
    """人类摘要只含结论与耗时枚举，且固定携带脱敏/不执行声明与退出码口径。"""
    evidence, _code = build_step_evidence(
        "search",
        Path("artifacts") / "search-smoke.json",
        bash="/fake/bash",
        runner=lambda argv: subprocess.CompletedProcess(argv, 0),
    )
    summary = format_step_summary(evidence)
    assert "RESULT: PASS" in summary
    assert "不读取任何敏感环境变量" in summary
    artifacts = _artifacts(tmp_path)
    steps = _write_three_steps(artifacts)
    aggregate, _code = build_provider_smoke_evidence(
        steps, artifacts / GATE_OUTPUT_FILE
    )
    text = format_aggregate_summary(aggregate)
    assert "voice=pass" in text and "search=pass" in text and "llm=pass" in text
    assert "RESULT: PASS" in text
    assert "不自动补跑" in text
