r"""M14-38 tools/ops/livekit_lan_cutover.py 契约测试：LAN cutover/rollback 安全边界，零真实改动。

覆盖（全部 FakeRunner 注入——零真实 Docker、不碰生产容器、不读真实 env）：
- plan 只读：九键 pin 报告 + 拓扑 delta 预览（IP/URL 非密钥可打印 old→new），
  零 compose、零 env 写；main 级契约：plan 真正零文件系统写（不建
  artifacts 目录、不写日志文件——与 --no-log-file 无关，stdout 照常）；
- 执行门：apply/rollback 缺/错 --confirm-rebuild（精确值 rebuild-api-livekit，
  不接受任何变体含尾空格）→ USAGE 零改动；apply 缺 --livekit-ip = argparse
  码 2；非法 IP / 非 ws(s) URL → USAGE；
- baseline gate：九键全绿放行；「仅缺拓扑键且既有键零漂移」首次采纳放行
  （AIOS_BIND_IP 从在线事实 HOST_BIND_IP 采纳补齐）；真实漂移（如
  AIOS_WEB_PORT）→ 拒绝零改动；
- apply happy path：备份（原字节 + sha256 锚定）→ 原子更新（secret 行原样、
  注释/行序保留）→ up 恰一次且恒为 -d --no-build --no-deps api livekit
  （绝不 web/redis/postgres/minio、绝不 --build；--no-deps 隔断 compose
  依赖解析，绝不连带重建 depends_on 依赖——2026-09-16 实测缺该 flag
  曾连带重建 minio 并因 aios/minio 镜像缺失失败）→ 健康等待（仅
  api/livekit）→ 九键复核全绿；
- 失败路径：compose config 失败 → 恢复备份零重建；up 失败/健康超时/复核
  不绿 → 打印确切 rollback 命令（不自动回滚）；
- rollback：伴生 sha256 校验先行（路径 = 备份名 -env.backup → -env.sha256；
  缺失/格式非法/文件名不匹配/哈希不匹配 → 拒绝且零副作用：零 Docker 调用、
  零 env 写、零 artifacts 创建）→ 备份九键校验（缺键/占位拒绝）→ 回滚前
  safety 快照 → 原子恢复 → 同样最小范围重建 + 复核；
- secret 不泄漏：伪 secret 标记绝不出现在任何日志行；
- 源码契约：绝不出现 stop/rm/kill/down/restart/reset/--build 带引号字面量；
  up 构造点唯一且恰含一个 "--no-deps" 引号字面量（无旁路拼装第二形态）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "livekit_lan_cutover.py"
SECRET_PATTERNS = ("sk-", "AKIA", "ghp_", "xoxb_", "-----BEGIN")
#: 伪 secret 标记（仅存在于 fake env/容器值里；断言绝不进入任何日志行）
MARK_AUTH = "ZX-markerauth-0123456789abcdef"
MARK_LIVEKIT = "ZX-markerlivekit-0123456789abcdef"
STACK_SERVICES = ("api", "livekit", "minio", "postgres", "redis", "web")

LAN_IP = "192.168.8.3"
LAN_WS = f"ws://{LAN_IP}:7880"
LOOPBACK_WS = "ws://127.0.0.1:7880"
CONFIRM = "rebuild-api-livekit"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


lc = _load_module(SCRIPT, "livekit_lan_cutover_under_test")


# ---------------------------------------------------------------- fakes

def env_text(bind: str = "127.0.0.1", lk_bind: str = "127.0.0.1",
             public: str = LOOPBACK_WS) -> str:
    return (
        "AIOS_IMAGE_TAG=m14-03-prod-rehearsal\n"
        "AIOS_WEB_IMAGE_TAG=m14-03-prod-rehearsal\n"
        "AIOS_APP_ENV=production\n"
        "AIOS_WEB_PORT=3011\n"
        f"AIOS_AUTH_SECRET={MARK_AUTH}\n"
        f"AIOS_LIVEKIT_API_SECRET={MARK_LIVEKIT}\n"
        f"AIOS_BIND_IP={bind}\n"
        f"AIOS_LIVEKIT_BIND_IP={lk_bind}\n"
        f"AIOS_PUBLIC_LIVEKIT_URL={public}\n"
    )


def live_env_text(host_bind: str = "127.0.0.1", public: str = LOOPBACK_WS) -> str:
    return (
        "APP_ENV=production\n"
        f"AUTH_SECRET={MARK_AUTH}\n"
        f"LIVEKIT_API_SECRET={MARK_LIVEKIT}\n"
        "S3_BUCKET=aios-objects\n"
        f"HOST_BIND_IP={host_bind}\n"
        f"PUBLIC_LIVEKIT_URL={public}\n"
    )


class FakeCutoverRunner:
    """伪 Docker/compose 命令面：up 前后事实可分别预制（重建后拓扑切换）。"""

    def __init__(self, *, baseline_live_env: str,
                 baseline_livekit_port: str = "127.0.0.1:7880",
                 post_up_live_env: str | None = None,
                 post_up_livekit_port: str | None = None,
                 post_up_ps_health: dict[str, str] | None = None,
                 up_rc: int = 0, config_rc: int = 0) -> None:
        self.baseline_live_env = baseline_live_env
        self.baseline_livekit_port = baseline_livekit_port
        self.post_up_live_env = post_up_live_env
        self.post_up_livekit_port = post_up_livekit_port
        self.post_up_ps_health = post_up_ps_health
        self.up_rc = up_rc
        self.config_rc = config_rc
        self.up_seen = False
        self.calls: list[tuple[str, ...]] = []

    def _cmd(self, argv, rc: int, out: str, err: str = ""):
        return lc.base.CommandResult(argv, rc, out, err)

    def run(self, argv, *, timeout: float = 60.0):
        argv = tuple(str(item) for item in argv)
        self.calls.append(argv)
        joined = " ".join(argv)
        if argv[:2] == ("docker", "version"):
            return self._cmd(argv, 0, "27.5.1\n")
        if "config" in argv and "--quiet" in argv:
            return self._cmd(argv, self.config_rc, "", "")
        if argv[:2] == ("docker", "inspect") and ".Config.Env" in joined:
            env = (self.post_up_live_env if self.up_seen
                   and self.post_up_live_env is not None else self.baseline_live_env)
            if env is None:  # api 容器不存在
                return self._cmd(argv, 1, "", "Error: No such object")
            return self._cmd(argv, 0, env)
        if argv[:2] == ("docker", "inspect"):
            target = str(argv[-1])
            image = ("aios/web:m14-03-prod-rehearsal" if "-web-" in target
                     else "aios/api:m14-03-prod-rehearsal")
            return self._cmd(argv, 0, image + "\n")
        if argv[:2] == ("docker", "port"):
            if "-livekit-" in str(argv[2]):
                port = (self.post_up_livekit_port if self.up_seen
                        and self.post_up_livekit_port is not None
                        else self.baseline_livekit_port)
                return self._cmd(argv, 0, port + "\n")
            return self._cmd(argv, 0, "127.0.0.1:3011\n")
        if "ps" in argv and "--format" in argv:
            health = (self.post_up_ps_health if self.up_seen
                      and self.post_up_ps_health is not None
                      else {name: "healthy" for name in STACK_SERVICES})
            rows = "".join(
                json.dumps({"Service": name, "State": "running", "Health": h}) + "\n"
                for name, h in sorted(health.items())
            )
            return self._cmd(argv, 0, rows)
        if "up" in argv:
            self.up_seen = True
            return self._cmd(argv, self.up_rc, "Container api-1 Recreated\n")
        return self._cmd(argv, 0, "")


def _args(argv: list[str]):
    return lc.build_parser().parse_args(argv)


def _apply_args(env_file: Path, *extra: str, confirm: str = CONFIRM):
    return _args(["apply", "--livekit-ip", LAN_IP, "--env-file", str(env_file),
                  "--engine-wait-seconds", "0", "--health-wait-seconds", "0",
                  *(["--confirm-rebuild", confirm] if confirm else []), *extra])


def _log() -> lc.base.RunLog:
    return lc.base.RunLog(
        redactions={MARK_AUTH: "***REDACTED:AIOS_AUTH_SECRET***",
                    MARK_LIVEKIT: "***REDACTED:AIOS_LIVEKIT_API_SECRET***"},
        echo=False)


def _no_leak(log: lc.base.RunLog) -> None:
    for line in log.lines:
        assert MARK_AUTH not in line, f"secret 标记泄漏到日志: {line}"
        assert MARK_LIVEKIT not in line, f"secret 标记泄漏到日志: {line}"


def _up_calls(runner: FakeCutoverRunner) -> list[tuple[str, ...]]:
    return [argv for argv in runner.calls if "up" in argv]


def _patch_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    target = tmp_path / "cutover-artifacts"
    monkeypatch.setattr(lc, "ARTIFACT_DIR", target)
    return target


#: 测试备份名——与 apply 产出同形态（伴生 .sha256 由 -env.backup 推导）
BACKUP_NAME = "20260916-170000-env.backup"


def _write_backup(directory: Path, content: str, *, name: str = BACKUP_NAME,
                  checksum_text: str | None = None) -> Path:
    """写备份 + 伴生 sha256（默认按 backup_env 写入端格式：摘要␣␣文件名）。"""
    backup = directory / name
    backup.write_text(content, encoding="utf-8", newline="\n")
    if checksum_text is None:
        digest = hashlib.sha256(backup.read_bytes()).hexdigest()
        checksum_text = f"{digest}  {name}\n"
    checksum = directory / name.replace("-env.backup", "-env.sha256")
    checksum.write_text(checksum_text, encoding="utf-8", newline="\n")
    return backup


# ---------------------------------------------------------------- plan 只读

def test_plan_read_only_reports_pins_without_touching(monkeypatch, tmp_path):
    _patch_artifacts(monkeypatch, tmp_path)
    original = env_text()
    env_file = tmp_path / "pin.env"
    env_file.write_text(original, encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(baseline_live_env=live_env_text())
    args = _args(["plan", "--env-file", str(env_file), "--engine-wait-seconds", "0"])
    log = _log()
    assert lc.run_plan(runner, log, args) == lc.EXIT_OK
    assert _up_calls(runner) == []
    assert env_file.read_text(encoding="utf-8") == original
    joined = "\n".join(log.lines)
    assert "只读" in joined and "结果: OK" in joined
    _no_leak(log)


def test_plan_delta_preview_prints_topology_change(monkeypatch, tmp_path):
    _patch_artifacts(monkeypatch, tmp_path)
    original = env_text()
    env_file = tmp_path / "pin.env"
    env_file.write_text(original, encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(baseline_live_env=live_env_text())
    args = _args(["plan", "--livekit-ip", LAN_IP, "--env-file", str(env_file),
                  "--engine-wait-seconds", "0"])
    log = _log()
    assert lc.run_plan(runner, log, args) == lc.EXIT_OK
    joined = "\n".join(log.lines)
    assert "AIOS_LIVEKIT_BIND_IP: 127.0.0.1 -> " + LAN_IP in joined
    assert f"AIOS_PUBLIC_LIVEKIT_URL: {LOOPBACK_WS} -> {LAN_WS}" in joined
    assert "预览" in joined
    assert _up_calls(runner) == []
    assert env_file.read_text(encoding="utf-8") == original  # 预览零改动
    _no_leak(log)


# ---------------------------------------------------------------- 执行门

@pytest.mark.parametrize("confirm", ["", "yes", "rebuild-api", "rebuild-api-livekit "])
def test_apply_confirm_gate_rejects_variants(monkeypatch, tmp_path, confirm):
    _patch_artifacts(monkeypatch, tmp_path)
    original = env_text()
    env_file = tmp_path / "pin.env"
    env_file.write_text(original, encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(baseline_live_env=live_env_text())
    log = _log()
    assert lc.run_apply(runner, log, _apply_args(env_file, confirm=confirm)) == lc.EXIT_USAGE
    assert runner.calls == []  # 门在最前——连 docker version 都不发
    assert env_file.read_text(encoding="utf-8") == original
    assert "拒绝" in "\n".join(log.lines)
    _no_leak(log)


def test_rollback_confirm_gate_rejects(monkeypatch, tmp_path):
    _patch_artifacts(monkeypatch, tmp_path)
    backup = tmp_path / "backup.env"
    backup.write_text(env_text(), encoding="utf-8", newline="\n")
    env_file = tmp_path / "pin.env"
    env_file.write_text(env_text(bind=LAN_IP, lk_bind=LAN_IP, public=LAN_WS),
                        encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(baseline_live_env=live_env_text(host_bind=LAN_IP, public=LAN_WS))
    args = _args(["rollback", "--backup-file", str(backup), "--env-file", str(env_file)])
    log = _log()
    assert lc.run_rollback(runner, log, args) == lc.EXIT_USAGE
    assert runner.calls == []


def test_apply_requires_livekit_ip_argparse_error():
    with pytest.raises(SystemExit) as exc:
        _args(["apply", "--confirm-rebuild", CONFIRM])
    assert exc.value.code == 2


def test_apply_invalid_ip_usage(monkeypatch, tmp_path):
    _patch_artifacts(monkeypatch, tmp_path)
    env_file = tmp_path / "pin.env"
    env_file.write_text(env_text(), encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(baseline_live_env=live_env_text())
    args = _apply_args(env_file)
    args.livekit_ip = "300.300.300.300"
    log = _log()
    assert lc.run_apply(runner, log, args) == lc.EXIT_USAGE
    assert _up_calls(runner) == []
    assert env_file.read_text(encoding="utf-8") == env_text()


def test_apply_invalid_public_url_usage(monkeypatch, tmp_path):
    _patch_artifacts(monkeypatch, tmp_path)
    env_file = tmp_path / "pin.env"
    env_file.write_text(env_text(), encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(baseline_live_env=live_env_text())
    args = _apply_args(env_file, "--public-url", "http://x")
    log = _log()
    assert lc.run_apply(runner, log, args) == lc.EXIT_USAGE
    assert _up_calls(runner) == []
    assert env_file.read_text(encoding="utf-8") == env_text()


# ---------------------------------------------------------------- baseline gate

def test_apply_adopts_missing_bind_ip_from_live_fact(monkeypatch, tmp_path):
    """首次采纳：env 缺 AIOS_BIND_IP（既有键零漂移、在线事实完整）→ gate 放行，
    BIND 从在线容器事实 HOST_BIND_IP 采纳补齐（不改变现有绑定）。"""
    _patch_artifacts(monkeypatch, tmp_path)
    original = env_text().replace("AIOS_BIND_IP=127.0.0.1\n", "")
    env_file = tmp_path / "pin.env"
    env_file.write_text(original, encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(
        baseline_live_env=live_env_text(),  # HOST_BIND_IP=127.0.0.1（在线事实）
        post_up_live_env=live_env_text(public=LAN_WS),
        post_up_livekit_port=f"{LAN_IP}:7880")
    log = _log()
    assert lc.run_apply(runner, log, _apply_args(env_file)) == lc.EXIT_OK
    joined = "\n".join(log.lines)
    assert "gate: baseline 允许" in joined and "AIOS_BIND_IP" in joined
    updated = env_file.read_text(encoding="utf-8")
    assert updated.endswith("AIOS_BIND_IP=127.0.0.1\n")  # 采纳补齐（原绑定不动）
    assert f"AIOS_LIVEKIT_BIND_IP={LAN_IP}\n" in updated
    _no_leak(log)


def test_apply_refuses_on_real_drift_zero_changes(monkeypatch, tmp_path):
    _patch_artifacts(monkeypatch, tmp_path)
    original = env_text().replace("AIOS_WEB_PORT=3011", "AIOS_WEB_PORT=3999")
    env_file = tmp_path / "pin.env"
    env_file.write_text(original, encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(baseline_live_env=live_env_text())
    log = _log()
    assert lc.run_apply(runner, log, _apply_args(env_file)) == lc.EXIT_ERROR
    joined = "\n".join(log.lines)
    assert "gate: baseline 拒绝" in joined and "AIOS_WEB_PORT" in joined
    assert _up_calls(runner) == []
    assert env_file.read_text(encoding="utf-8") == original  # 绝不带病覆盖
    _no_leak(log)


# ---------------------------------------------------------------- apply 端到端

def _run_apply(env_file: Path, runner: FakeCutoverRunner, *extra: str):
    log = _log()
    code = lc.run_apply(runner, log, _apply_args(env_file, *extra))
    return code, log


def test_apply_happy_path_full(monkeypatch, tmp_path):
    _patch_artifacts(monkeypatch, tmp_path)
    artifacts = lc.ARTIFACT_DIR
    original = env_text()
    env_file = tmp_path / "pin.env"
    env_file.write_text(original, encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(
        baseline_live_env=live_env_text(),
        post_up_live_env=live_env_text(public=LAN_WS),
        post_up_livekit_port=f"{LAN_IP}:7880")
    code, log = _run_apply(env_file, runner)
    assert code == lc.EXIT_OK
    ups = _up_calls(runner)
    assert len(ups) == 1
    assert ups[0][ups[0].index("up") + 1:] == ("-d", "--no-build", "--no-deps", "api", "livekit")
    assert "--build" not in ups[0]  # 成员检查：--no-build 不含 --build 子串
    for forbidden in ("web", "redis", "postgres", "minio"):
        assert forbidden not in ups[0]
    updated = env_file.read_text(encoding="utf-8")
    assert f"AIOS_LIVEKIT_BIND_IP={LAN_IP}\n" in updated
    assert f"AIOS_PUBLIC_LIVEKIT_URL={LAN_WS}\n" in updated
    assert f"AIOS_AUTH_SECRET={MARK_AUTH}\n" in updated  # secret 行原样
    backups = sorted(artifacts.glob("*-env.backup"))
    assert len(backups) == 1 and backups[0].read_bytes() == original.encode("utf-8")
    checksums = sorted(artifacts.glob("*-env.sha256"))
    assert len(checksums) == 1  # sha256 完整性锚定伴生文件
    joined = "\n".join(log.lines)
    assert "结果: OK" in joined and "回滚锚点" in joined
    _no_leak(log)


def test_apply_config_failure_restores_backup_no_rebuild(monkeypatch, tmp_path):
    _patch_artifacts(monkeypatch, tmp_path)
    original = env_text()
    env_file = tmp_path / "pin.env"
    env_file.write_text(original, encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(baseline_live_env=live_env_text(), config_rc=1)
    code, log = _run_apply(env_file, runner)
    assert code == lc.EXIT_ERROR
    assert _up_calls(runner) == []  # 校验失败 → 恢复备份、零重建
    assert env_file.read_text(encoding="utf-8") == original
    assert any("校验失败" in line for line in log.lines)
    _no_leak(log)


def test_apply_up_failure_prints_exact_rollback_hint(monkeypatch, tmp_path):
    _patch_artifacts(monkeypatch, tmp_path)
    env_file = tmp_path / "pin.env"
    env_file.write_text(env_text(), encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(baseline_live_env=live_env_text(), up_rc=1)
    code, log = _run_apply(env_file, runner)
    assert code == lc.EXIT_ERROR
    joined = "\n".join(log.lines)
    assert "rollback --backup-file" in joined and CONFIRM in joined
    updated = env_file.read_text(encoding="utf-8")
    assert f"AIOS_LIVEKIT_BIND_IP={LAN_IP}\n" in updated  # env 已切换（不自动回滚）
    _no_leak(log)


def test_apply_health_timeout_prints_hint(monkeypatch, tmp_path):
    _patch_artifacts(monkeypatch, tmp_path)
    env_file = tmp_path / "pin.env"
    env_file.write_text(env_text(), encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(
        baseline_live_env=live_env_text(),
        post_up_live_env=live_env_text(public=LAN_WS),
        post_up_livekit_port=f"{LAN_IP}:7880",
        post_up_ps_health={**{name: "healthy" for name in STACK_SERVICES}, "api": "starting"})
    code, log = _run_apply(env_file, runner)
    assert code == lc.EXIT_ERROR
    joined = "\n".join(log.lines)
    assert "rollback --backup-file" in joined and "api" in joined
    _no_leak(log)


def test_apply_final_pin_mismatch_fails_with_hint(monkeypatch, tmp_path):
    """重建后容器公开 URL 仍 loopback（拓扑未生效）→ 九键复核不绿 → 可见失败。"""
    _patch_artifacts(monkeypatch, tmp_path)
    env_file = tmp_path / "pin.env"
    env_file.write_text(env_text(), encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(
        baseline_live_env=live_env_text(),
        post_up_live_env=live_env_text(public=LOOPBACK_WS),  # 容器未接 LAN 拓扑
        post_up_livekit_port=f"{LAN_IP}:7880")
    code, log = _run_apply(env_file, runner)
    assert code == lc.EXIT_ERROR
    joined = "\n".join(log.lines)
    assert "复核" in joined and "AIOS_PUBLIC_LIVEKIT_URL" in joined
    assert "rollback --backup-file" in joined
    _no_leak(log)


# ---------------------------------------------------------------- rollback

def _rollback_args(env_file: Path, backup: Path):
    return _args(["rollback", "--backup-file", str(backup), "--env-file", str(env_file),
                  "--engine-wait-seconds", "0", "--health-wait-seconds", "0",
                  "--confirm-rebuild", CONFIRM])


def test_rollback_happy_path_restores_and_rebuilds(monkeypatch, tmp_path):
    _patch_artifacts(monkeypatch, tmp_path)
    artifacts = lc.ARTIFACT_DIR
    lan_text = env_text(bind=LAN_IP, lk_bind=LAN_IP, public=LAN_WS)
    loopback_text = env_text()
    backup = _write_backup(tmp_path, loopback_text)
    env_file = tmp_path / "pin.env"
    env_file.write_text(lan_text, encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(
        baseline_live_env=live_env_text(host_bind=LAN_IP, public=LAN_WS),
        post_up_live_env=live_env_text(),
        post_up_livekit_port="127.0.0.1:7880")
    log = _log()
    assert lc.run_rollback(runner, log, _rollback_args(env_file, backup)) == lc.EXIT_OK
    assert env_file.read_text(encoding="utf-8") == loopback_text  # 字节级恢复
    ups = _up_calls(runner)
    assert len(ups) == 1
    assert ups[0][ups[0].index("up") + 1:] == ("-d", "--no-build", "--no-deps", "api", "livekit")
    safety = sorted(artifacts.glob("*-env.backup"))
    assert len(safety) == 1 and safety[0].read_bytes() == lan_text.encode("utf-8")
    joined = "\n".join(log.lines)
    assert "结果: OK" in joined and "回滚前快照" in joined
    _no_leak(log)


def test_rollback_missing_backup_file_refuses(monkeypatch, tmp_path):
    _patch_artifacts(monkeypatch, tmp_path)
    env_file = tmp_path / "pin.env"
    env_file.write_text(env_text(), encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(baseline_live_env=live_env_text())
    log = _log()
    code = lc.run_rollback(runner, log, _rollback_args(env_file, tmp_path / "absent.env"))
    assert code == lc.EXIT_ERROR
    assert _up_calls(runner) == []


def test_rollback_backup_missing_pin_keys_refuses(monkeypatch, tmp_path):
    _patch_artifacts(monkeypatch, tmp_path)
    backup = _write_backup(tmp_path, "AIOS_IMAGE_TAG=x\n")  # sha256 合法但缺键
    original = env_text()
    env_file = tmp_path / "pin.env"
    env_file.write_text(original, encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(baseline_live_env=live_env_text())
    log = _log()
    code = lc.run_rollback(runner, log, _rollback_args(env_file, backup))
    assert code == lc.EXIT_ERROR
    assert _up_calls(runner) == []
    assert env_file.read_text(encoding="utf-8") == original  # 校验失败零改动
    joined = "\n".join(log.lines)
    assert "AIOS_BIND_IP" in joined and "AIOS_AUTH_SECRET" in joined  # 缺失键名可见
    assert "AIOS_IMAGE_TAG" not in joined  # 在场键不报（值 x 更不回显）


# ------------------------------------------------- rollback 伴生 sha256 fail-closed

def _rollback_reject_setup(monkeypatch, tmp_path) -> tuple[
        FakeCutoverRunner, lc.base.RunLog, Path, str]:
    """公共布置：原始 env + FakeRunner；返回 (runner, log, env_file, original)。"""
    _patch_artifacts(monkeypatch, tmp_path)
    original = env_text()
    env_file = tmp_path / "pin.env"
    env_file.write_text(original, encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(baseline_live_env=live_env_text())
    return runner, _log(), env_file, original


def _assert_zero_side_effects(runner, log, env_file, original, artifacts,
                              refusal: str) -> None:
    """四态共用断言：拒绝 + 零 Docker / 零 env 写 / 零 artifacts 创建。"""
    joined = "\n".join(log.lines)
    assert refusal in joined
    assert runner.calls == []  # 校验先于 Docker——连 docker version 都不发
    assert env_file.read_text(encoding="utf-8") == original  # env 未动
    assert not artifacts.exists()  # 未建 safety 备份、未建 artifacts 目录
    _no_leak(log)


def test_rollback_missing_companion_checksum_refuses(monkeypatch, tmp_path):
    """四态 1/4：伴生 .sha256 缺失 → 拒绝，零副作用（checksum 先于一切）。"""
    runner, log, env_file, original = _rollback_reject_setup(monkeypatch, tmp_path)
    artifacts = lc.ARTIFACT_DIR
    backup = tmp_path / BACKUP_NAME  # 只写备份、不写伴生 sha256
    backup.write_text(env_text(), encoding="utf-8", newline="\n")
    code = lc.run_rollback(runner, log, _rollback_args(env_file, backup))
    assert code == lc.EXIT_ERROR
    _assert_zero_side_effects(runner, log, env_file, original, artifacts,
                              "伴生 sha256 缺失")


def test_rollback_invalid_checksum_format_refuses(monkeypatch, tmp_path):
    """四态 2/4：单空格分隔（非 sha256sum 两空格格式）→ 拒绝，零副作用。"""
    runner, log, env_file, original = _rollback_reject_setup(monkeypatch, tmp_path)
    artifacts = lc.ARTIFACT_DIR
    digest = hashlib.sha256(env_text().encode("utf-8")).hexdigest()
    backup = _write_backup(tmp_path, env_text(),
                           checksum_text=f"{digest} {BACKUP_NAME}\n")  # 单空格
    code = lc.run_rollback(runner, log, _rollback_args(env_file, backup))
    assert code == lc.EXIT_ERROR
    _assert_zero_side_effects(runner, log, env_file, original, artifacts,
                              "格式非法")


def test_rollback_checksum_filename_mismatch_refuses(monkeypatch, tmp_path):
    """四态 3/4：伴生文件记录的文件名 ≠ 备份实际名（嫁接）→ 拒绝，零副作用。"""
    runner, log, env_file, original = _rollback_reject_setup(monkeypatch, tmp_path)
    artifacts = lc.ARTIFACT_DIR
    digest = hashlib.sha256(env_text().encode("utf-8")).hexdigest()
    backup = _write_backup(
        tmp_path, env_text(),
        checksum_text=f"{digest}  20260916-999999-env.backup\n")  # 名不对
    code = lc.run_rollback(runner, log, _rollback_args(env_file, backup))
    assert code == lc.EXIT_ERROR
    _assert_zero_side_effects(runner, log, env_file, original, artifacts,
                              "文件名不匹配")


def test_rollback_checksum_hash_mismatch_refuses(monkeypatch, tmp_path):
    """四态 4/4：记录摘要 ≠ 备份字节哈希（篡改/截断）→ 拒绝，零副作用。"""
    runner, log, env_file, original = _rollback_reject_setup(monkeypatch, tmp_path)
    artifacts = lc.ARTIFACT_DIR
    backup = _write_backup(tmp_path, env_text(),
                           checksum_text=f"{'0' * 64}  {BACKUP_NAME}\n")  # 摘要不符
    code = lc.run_rollback(runner, log, _rollback_args(env_file, backup))
    assert code == lc.EXIT_ERROR
    _assert_zero_side_effects(runner, log, env_file, original, artifacts,
                              "sha256 不匹配")


# ------------------------------------------------- main 级 plan 零文件系统写契约

def test_main_plan_zero_filesystem_write(monkeypatch, tmp_path, capsys):
    """main 级契约：plan 真正零文件系统写——不建 ARTIFACT_DIR、不写日志文件
    （与 --no-log-file 无关——不传该 flag 也绝不写）；stdout 照常输出。"""
    artifacts = _patch_artifacts(monkeypatch, tmp_path)
    env_file = tmp_path / "pin.env"
    env_file.write_text(env_text(), encoding="utf-8", newline="\n")
    runner = FakeCutoverRunner(baseline_live_env=live_env_text())
    monkeypatch.setattr(lc.base, "RealRunner", lambda: runner)
    assert lc.main(["plan", "--env-file", str(env_file),
                    "--engine-wait-seconds", "0"]) == lc.EXIT_OK
    assert not artifacts.exists()  # 未建 artifacts 目录
    assert [item.name for item in tmp_path.iterdir()] == ["pin.env"]  # 零新增文件
    out = capsys.readouterr().out
    assert "只读" in out and "结果: OK" in out  # stdout 保留
    assert MARK_AUTH not in out and MARK_LIVEKIT not in out  # secret 不上屏


# ---------------------------------------------------------------- 原子更新纯函数

def test_atomic_update_env_lf_preserves_layout_and_appends(tmp_path):
    text = (
        "# 注释行保留\n"
        "AIOS_WEB_PORT=3011\n"
        f"AIOS_AUTH_SECRET={MARK_AUTH}\n"
        "AIOS_LIVEKIT_BIND_IP=127.0.0.1\n"
        "AIOS_PUBLIC_LIVEKIT_URL=" + LOOPBACK_WS  # 末行无 EOL
    )
    env_file = tmp_path / "lf.env"
    env_file.write_text(text, encoding="utf-8", newline="\n")
    lc.atomic_update_env(env_file, {"AIOS_LIVEKIT_BIND_IP": LAN_IP,
                                    "AIOS_BIND_IP": LAN_IP})
    expected = (
        "# 注释行保留\n"
        "AIOS_WEB_PORT=3011\n"
        f"AIOS_AUTH_SECRET={MARK_AUTH}\n"
        f"AIOS_LIVEKIT_BIND_IP={LAN_IP}\n"
        f"AIOS_PUBLIC_LIVEKIT_URL={LOOPBACK_WS}\n"  # 追加前补 EOL
        f"AIOS_BIND_IP={LAN_IP}\n"  # 缺失键按主导 EOL 追加
    )
    assert env_file.read_text(encoding="utf-8") == expected


def test_atomic_update_env_crlf_preserved(tmp_path):
    text = ("AIOS_LIVEKIT_BIND_IP=127.0.0.1\r\n"
            f"AIOS_AUTH_SECRET={MARK_AUTH}\r\n")
    env_file = tmp_path / "crlf.env"
    env_file.write_bytes(text.encode("utf-8"))
    lc.atomic_update_env(env_file, {"AIOS_LIVEKIT_BIND_IP": LAN_IP,
                                    "AIOS_BIND_IP": LAN_IP})
    with env_file.open("r", encoding="utf-8", newline="") as handle:
        content = handle.read()
    assert f"AIOS_LIVEKIT_BIND_IP={LAN_IP}\r\n" in content  # 替换行保留 CRLF
    assert f"AIOS_AUTH_SECRET={MARK_AUTH}\r\n" in content  # secret 行字节原样
    assert content.endswith(f"AIOS_BIND_IP={LAN_IP}\r\n")  # 追加亦用主导 EOL


# ---------------------------------------------------------------- 源码契约

def test_source_never_issues_destructive_subcommands():
    source = SCRIPT.read_text(encoding="utf-8")
    for literal in ('"down"', '"stop"', '"kill"', '"rm"', '"restart"', '"reset"',
                    '"--build"'):
        assert literal not in source, f"禁止出现的字面量: {literal}"
    for pattern in SECRET_PATTERNS:
        assert pattern not in source
    assert lc.REBUILD_SERVICES == ("api", "livekit")
    assert lc.REBUILD_CONFIRM_TOKEN == "rebuild-api-livekit"
    # up 构造点唯一且恰含一个 --no-deps（无旁路拼装第二形态、无遗漏依赖隔断）
    assert source.count('"up"') == 1, "up 子命令构造点必须唯一"
    assert source.count('"--no-deps"') == 1, "up 必须恰含一个 --no-deps 字面量"


def test_parser_defaults():
    plan_args = _args(["plan"])
    assert plan_args.project == "aios-m14-03-production-rehearsal"
    assert plan_args.profile == "local"
    assert plan_args.livekit_ip == "" and plan_args.public_url == "" and plan_args.bind_ip == ""
    apply_args = _args(["apply", "--livekit-ip", "1.2.3.4"])
    assert apply_args.confirm_rebuild == ""  # 缺省即拒绝（执行门）
    assert apply_args.health_wait_seconds == lc.base.DEFAULT_HEALTH_WAIT_SECONDS
