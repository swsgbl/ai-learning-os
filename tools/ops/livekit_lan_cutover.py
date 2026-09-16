#!/usr/bin/env python
"""M14-38 LiveKit LAN cutover/rollback 可重复安全工具。

设计（与 tools/ops/production_recovery.py 同款纪律：单文件、纯标准库、
平台命令经 Runner 注入，测试注入 FakeRunner——零真实 Docker、不碰生产
容器、不读真实 env）：

- 复用 production_recovery（spec_from_file_location 导入）：Runner/RealRunner/
  RunLog/parse_env_file/check_pins/collect_live_pins/placeholder_pin_keys/
  validate_compose/wait_docker_engine/wait_stack_healthy + 九键 PIN/
  TOPOLOGY_PIN_KEYS——pin 语义单一事实源，本工具不复制实现。
- 子命令：
    plan（默认只读）：报告当前九键 pin 状态；给 --livekit-ip/--public-url/
      --bind-ip 时预览拓扑键 delta（IP/URL 非密钥，可打印 old→new；secret
      键永远只出键名）；零文件系统写（不建 artifacts 目录、不写日志文件，
      stdout 照常）、零 compose。
    apply：把信令/媒体面切到 LAN——baseline gate → 备份（+SHA256）→
      原子更新非密钥拓扑键（secret 行字节原样保留）→ compose config 静态
      校验（失败即恢复备份中止，零重建）→ 最小范围重建（up -d
      --no-build --no-deps 仅 api livekit——--no-deps 显式隔断 compose
      依赖解析，绝不连带重建 depends_on 依赖；web/redis/postgres/minio
      绝不触碰）→ 健康等待（仅 api/livekit）→ 九键复核必须全绿（失败
      打印确切回滚命令，不自动回滚）。
    rollback --backup-file <path>：先校验伴生 sha256（路径 = 备份名
      -env.backup → -env.sha256，与备份写入端镜像对称；缺失/格式非法/
      文件名不匹配/哈希不匹配任一即拒绝——拒绝时零副作用：不读备份 pin
      值、不建 safety 备份、不调 Docker、不写 env）→ 校验备份九键齐全且
      无占位值 → 回滚前再备份当前（可再回滚）→ 原子恢复 → 同样最小范围
      重建 + 复核。
- 执行门（apply/rollback）：--confirm-rebuild rebuild-api-livekit 精确值，
  否则 USAGE（码 2）零改动；无子命令 = argparse 报错（码 2）。
- baseline gate（apply）：当前九键 pin 必须全绿；唯一例外 = 仅缺拓扑键
  且既有键零漂移、在线事实完整（首次把拓扑键补进 pin 文件的采纳场景——
  AIOS_BIND_IP 缺失时从在线容器事实 HOST_BIND_IP 采纳补齐）。其余任何
  漂移/占位/事实缺失拒绝，绝不带病覆盖。
- secret 边界：AIOS_AUTH_SECRET/AIOS_LIVEKIT_API_SECRET 值永不进入日志/
  输出（RunLog redactions 防御性脱敏）；原子更新只触碰三拓扑键；备份
  文件与 env 同为 gitignored artifacts 目录 + sha256 完整性锚定，日志只
  打印路径与摘要。
- 不可触碰边界（源码契约测试锁定）：本工具绝不构造 stop/rm/kill/down/
  restart/reset 子命令；绝不 --build；绝不触碰 web/redis/postgres/minio
  服务；重建恒为 up -d --no-build --no-deps api livekit（--no-deps：
  compose 依赖解析绝不连带重建 depends_on 依赖——缺该 flag 时实测曾
  尝试连带重建 minio 并因 aios/minio 镜像缺失失败；compose 幂等：
  配置未变即 no-op）；Windows 侧无弹窗纪律继承 RealRunner。

退出码：0 完成/plan 只读通过；1 可见失败（gate 拒绝/校验失败/健康未达/
pin 复核不绿/备份缺失/伴生 sha256 校验失败）；2 参数错误（含缺
--confirm-rebuild）。

用法（仓库根）：
  python tools/ops/livekit_lan_cutover.py plan --livekit-ip 192.168.8.3
  python tools/ops/livekit_lan_cutover.py apply --livekit-ip 192.168.8.3 \\
      --confirm-rebuild rebuild-api-livekit
  python tools/ops/livekit_lan_cutover.py rollback --backup-file <path> \\
      --confirm-rebuild rebuild-api-livekit
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import ipaddress
import os
import re
import shutil
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
RECOVERY_SCRIPT = REPO_ROOT / "tools" / "ops" / "production_recovery.py"
COMPOSE_FILE = REPO_ROOT / "infra" / "docker-compose.yml"
ARTIFACT_DIR = REPO_ROOT / "artifacts" / "ops" / "livekit-cutover"

#: 执行门精确令牌（防手滑/防脚本误用：不接受任何变体）
REBUILD_CONFIRM_TOKEN = "rebuild-api-livekit"
#: 最小范围重建服务集（其余服务绝不触碰）
REBUILD_SERVICES: tuple[str, ...] = ("api", "livekit")


def _load_recovery_module():
    spec = importlib.util.spec_from_file_location(
        "production_recovery_for_cutover", RECOVERY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


#: production_recovery 复用面（模块级一次性加载；其顶层零副作用）
base = _load_recovery_module()


# ---------------------------------------------------------------- 参数解析

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="livekit_lan_cutover.py",
        description="LiveKit LAN cutover/rollback：plan 只读；apply/rollback 需显式确认，"
                    "最小范围重建 api+livekit（其余服务绝不触碰）")
    subs = parser.add_subparsers(dest="command", required=True)

    def add_common(sub: argparse.ArgumentParser) -> argparse.ArgumentParser:
        sub.add_argument("--project", default=base.DEFAULT_PROJECT,
                         help=f"compose 项目名（默认 {base.DEFAULT_PROJECT}）")
        sub.add_argument("--profile", default=base.DEFAULT_PROFILE,
                         help=f"compose profile（默认 {base.DEFAULT_PROFILE}）")
        sub.add_argument("--env-file", type=Path, default=base.DEFAULT_ENV_FILE,
                         help="部署 pin env 文件（默认 infra/env.production-recovery）")
        sub.add_argument("--engine-wait-seconds", type=int,
                         default=base.DEFAULT_ENGINE_WAIT_SECONDS)
        sub.add_argument("--no-log-file", action="store_true",
                         help="不写 artifacts 日志文件（stdout 照常；plan 恒不写——只读零文件系统写）")
        return sub

    plan_cmd = add_common(subs.add_parser(
        "plan", help="只读体检 + 切换 delta 预览（零改动零重建）"))
    plan_cmd.add_argument("--livekit-ip", default="",
                          help="目标 LiveKit 宿主绑定 IP（IPv4 点分四段）")
    plan_cmd.add_argument("--public-url", default="",
                          help="浏览器可达 ws/wss URL（默认 ws://<livekit-ip>:7880）")
    plan_cmd.add_argument("--bind-ip", default="",
                          help="宿主绑定 IP（默认不动；仅 env 缺 AIOS_BIND_IP 时从在线事实采纳）")

    apply_cmd = add_common(subs.add_parser(
        "apply", help=f"切换 LAN 拓扑 + 最小范围重建（需 --confirm-rebuild {REBUILD_CONFIRM_TOKEN}）"))
    apply_cmd.add_argument("--livekit-ip", required=True, help="目标 LiveKit 宿主绑定 IP（必需）")
    apply_cmd.add_argument("--public-url", default="",
                           help="浏览器可达 ws/wss URL（默认 ws://<livekit-ip>:7880）")
    apply_cmd.add_argument("--bind-ip", default="",
                           help="宿主绑定 IP（默认不动；仅 env 缺 AIOS_BIND_IP 时从在线事实采纳）")
    apply_cmd.add_argument("--health-wait-seconds", type=int,
                           default=base.DEFAULT_HEALTH_WAIT_SECONDS)
    apply_cmd.add_argument("--confirm-rebuild", default="",
                           help=f"执行门（必需精确值 {REBUILD_CONFIRM_TOKEN}）")

    rollback_cmd = add_common(subs.add_parser(
        "rollback", help=f"从备份恢复 + 最小范围重建（需 --confirm-rebuild {REBUILD_CONFIRM_TOKEN}）"))
    rollback_cmd.add_argument("--backup-file", type=Path, required=True,
                              help="apply 产出的 env 备份文件路径")
    rollback_cmd.add_argument("--health-wait-seconds", type=int,
                              default=base.DEFAULT_HEALTH_WAIT_SECONDS)
    rollback_cmd.add_argument("--confirm-rebuild", default="",
                              help=f"执行门（必需精确值 {REBUILD_CONFIRM_TOKEN}）")
    return parser


# ---------------------------------------------------------------- 更新集推导

def resolve_updates(args: argparse.Namespace, env_values: dict[str, str],
                    live: dict[str, str]) -> tuple[dict[str, str], str]:
    """由参数推导拓扑键更新集（仅三拓扑键；secret 键绝不触碰）。

    返回 (updates, error)：error 非空 = 参数非法（USAGE 语义）。
    AIOS_BIND_IP 缺省不动——仅当 env 缺该键时从在线容器事实 HOST_BIND_IP
    采纳补齐（首次把拓扑键补进 pin 文件的采纳场景，不改变现有绑定）。
    """
    if not args.livekit_ip:
        return {}, "--livekit-ip 必需（apply / 带参数 plan）"
    try:
        ipaddress.IPv4Address(args.livekit_ip)
    except ValueError:
        return {}, f"--livekit-ip 非法（IPv4 点分四段）: {args.livekit_ip!r}"
    updates: dict[str, str] = {"AIOS_LIVEKIT_BIND_IP": args.livekit_ip}
    public_url = args.public_url or f"ws://{args.livekit_ip}:7880"
    parsed = urllib.parse.urlparse(public_url)
    if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
        return {}, f"--public-url 非法（仅 ws://|wss:// + 非空主机名）: {public_url!r}"
    updates["AIOS_PUBLIC_LIVEKIT_URL"] = public_url
    if args.bind_ip:
        try:
            ipaddress.IPv4Address(args.bind_ip)
        except ValueError:
            return {}, f"--bind-ip 非法（IPv4 点分四段）: {args.bind_ip!r}"
        updates["AIOS_BIND_IP"] = args.bind_ip
    elif not env_values.get("AIOS_BIND_IP"):
        live_bind = live.get("AIOS_BIND_IP") or ""
        if not live_bind:
            return {}, "env 缺 AIOS_BIND_IP 且在线容器无 HOST_BIND_IP 事实——请显式 --bind-ip"
        updates["AIOS_BIND_IP"] = live_bind
    return updates, ""


def _print_delta(log: base.RunLog, env_values: dict[str, str],
                 updates: dict[str, str]) -> None:
    """拓扑键 delta：IP/URL 非密钥，可打印 old→new（secret 键永不在此）。"""
    for key in base.TOPOLOGY_PIN_KEYS:
        if key in updates:
            old = env_values.get(key) or "(缺失)"
            log.say(f"delta: {key}: {old} -> {updates[key]}")


def baseline_gate(report: base.PinReport, log: base.RunLog) -> bool:
    """apply 前置门：九键全绿，或「仅缺拓扑键且既有键零漂移、在线事实完整」
    的首次采纳形态；其余任何漂移/占位/事实缺失拒绝——绝不带病覆盖。

    注：check_pins 对「env 缺键」会同时计入 mismatched（None != 在线值），
    故采纳形态须剔除缺键派生的假阳性后再判定真漂移。
    """
    if report.ok:
        log.say("gate: baseline 九键全绿——允许切换")
        return True
    real_drift = tuple(
        key for key in report.mismatched_keys if key not in report.missing_keys
    )
    adoptable = (
        not real_drift and not report.placeholder_keys
        and not report.missing_live_keys
        and set(report.missing_keys) <= set(base.TOPOLOGY_PIN_KEYS)
    )
    if adoptable:
        log.say(f"gate: baseline 允许（仅缺拓扑键: {', '.join(report.missing_keys)}"
                "——首次采纳场景，切换时补齐；既有键与在线事实零漂移）")
        return True
    log.say("gate: baseline 拒绝——pin 未就绪且不属于「仅缺拓扑键」可采纳形态（键名见上）")
    log.say("      先人工核实 env 与在线容器（可跑 production_recovery --dry-run 看键名报告）")
    return False


# ---------------------------------------------------------------- env 文件写路径

def backup_env(env_file: Path, log: base.RunLog) -> Path:
    """备份 env 文件到 gitignored artifacts 目录 + sha256 完整性锚定。

    备份含 secret（与 env 文件同级敏感度），但路径与 env 同处 gitignored
    树；日志只打印路径与摘要前缀，绝不打印内容。
    """
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    backup = ARTIFACT_DIR / f"{stamp}-env.backup"
    suffix = 0
    while backup.exists():  # 同秒重入（rollback 的回滚前快照等）不覆盖
        suffix += 1
        backup = ARTIFACT_DIR / f"{stamp}-{suffix}-env.backup"
    shutil.copy2(env_file, backup)
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    checksum = backup.with_name(backup.name.replace("-env.backup", "-env.sha256"))
    checksum.write_text(f"{digest}  {backup.name}\n", encoding="utf-8", newline="\n")
    log.say(f"备份: {backup}（sha256={digest[:16]}…）")
    return backup


def verify_backup_checksum(backup: Path, log: base.RunLog) -> bool:
    """备份伴生 sha256 完整性校验（回滚路径的第一道门，先于一切副作用）。

    伴生路径由备份名推导（-env.backup → -env.sha256，与 backup_env 写入端
    镜像对称）。四态 fail-closed，任一即拒绝且此前零副作用（尚未读 pin 值/
    未建 safety 备份/未调 Docker/未写 env）：
    1. 伴生文件缺失（备份来源不可证明）；
    2. 格式非法（非单行 ``<64 位十六进制>`` + 两空格 + ``<文件名>``）；
    3. 文件名不匹配（伴生文件记录的文件名 ≠ 备份实际名——防跨备份嫁接）；
    4. 哈希不匹配（备份字节与记录摘要不符——防篡改/截断/部分写入）。
    """
    checksum = backup.with_name(backup.name.replace("-env.backup", "-env.sha256"))
    if not checksum.is_file():
        log.say(f"rollback: 伴生 sha256 缺失（{checksum.name}）——拒绝执行（备份完整性无法证明）")
        return False
    lines = [line for line in
             checksum.read_text(encoding="utf-8", errors="replace").splitlines()
             if line.strip()]
    if len(lines) != 1:
        log.say(f"rollback: 伴生 sha256 格式非法（应为单行「摘要␣␣文件名」，实得 {len(lines)} 行）——拒绝执行")
        return False
    record = re.fullmatch(r"([0-9a-f]{64})  (.+)", lines[0])
    if record is None:
        log.say("rollback: 伴生 sha256 格式非法（非「64 位十六进制 + 两空格 + 文件名」）——拒绝执行")
        return False
    digest, recorded_name = record.group(1), record.group(2)
    if recorded_name != backup.name:
        log.say(f"rollback: 伴生 sha256 文件名不匹配（记录 {recorded_name} ≠ 备份 {backup.name}）——拒绝执行")
        return False
    actual = hashlib.sha256(backup.read_bytes()).hexdigest()
    if actual != digest:
        log.say(f"rollback: sha256 不匹配（备份疑被篡改/截断——期望 {digest[:16]}… 实得 {actual[:16]}…）——拒绝执行")
        return False
    log.say(f"完整性: 备份伴生 sha256 校验通过（{checksum.name}）")
    return True


def restore_from_backup(env_file: Path, backup: Path, log: base.RunLog) -> None:
    """原子恢复：备份内容字节级写回（同目录临时文件 + os.replace）。"""
    data = backup.read_bytes()
    temp = env_file.with_name(env_file.name + ".tmp-cutover")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, env_file)
    log.say(f"env 已原子恢复自备份: {backup}")


def _restore_or_report(env_file: Path, backup: Path, log: base.RunLog) -> None:
    try:
        restore_from_backup(env_file, backup, log)
    except OSError as nested:
        log.say(f"备份恢复失败（{type(nested).__name__}）——人工核实备份: {backup}")


def atomic_update_env(env_file: Path, updates: dict[str, str]) -> None:
    """原子更新非密钥拓扑键：同目录临时文件 + os.replace。

    保留行序/注释/EOL（LF 或 CRLF 按原文件主导形态）；secret 行与未涉及行
    字节原样；updates 中缺失的键按主导 EOL 追加到文件尾。
    """
    with env_file.open("r", encoding="utf-8", newline="") as handle:
        lines = handle.readlines()
    eol = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
    pending = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = None
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
        if key in pending:
            tail = line[len(line.rstrip("\r\n")):] or eol
            out.append(f"{key}={pending.pop(key)}{tail}")
        else:
            out.append(line)
    if pending:
        if out and not out[-1].endswith(("\n", "\r\n")):
            out[-1] += eol
        for key in sorted(pending):
            out.append(f"{key}={pending[key]}{eol}")
    temp = env_file.with_name(env_file.name + ".tmp-cutover")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        handle.write("".join(out))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, env_file)


# ---------------------------------------------------------------- 重建与复核

def compose_up_limited(runner: base.Runner, log: base.RunLog, compose_file: Path,
                       project: str, profile: str, env_file: Path) -> bool:
    """最小范围重建：up -d --no-build --no-deps 仅 api livekit。

    --no-deps 显式隔断 compose 依赖解析：缺该 flag 时 up 虽只点名
    api livekit，仍会连带重建 depends_on 依赖（minio 等）——M14-38
    实测即因 aios/minio 镜像缺失连带失败，违反「只碰 api+livekit」
    范围契约。compose 幂等（配置未变即 no-op）；绝不 --build；绝不
    触碰 web/redis/postgres/minio。
    """
    argv = base._compose_base(compose_file, project, profile, env_file) + [
        "up", "-d", "--no-build", "--no-deps", *REBUILD_SERVICES]
    label = f"compose up -d --no-build --no-deps {'+'.join(REBUILD_SERVICES)}"
    result = runner.run(argv, timeout=600.0)
    if result.returncode != 0:
        log.say(f"{label}: rc={result.returncode}——可见失败")
        log.say(log.redact((result.stderr or result.stdout).strip()[-400:]))
        return False
    log.say(f"{label}: 已执行（最小范围重建；依赖与其余服务不触碰）")
    return True


def _say_rollback_hint(log: base.RunLog, backup: Path) -> None:
    log.say("如需回退（不自动回滚，交人工裁决）:")
    log.say(f"  python tools/ops/livekit_lan_cutover.py rollback --backup-file {backup}"
            f" --confirm-rebuild {REBUILD_CONFIRM_TOKEN}")


def _rebuild_and_verify(runner: base.Runner, log: base.RunLog,
                        args: argparse.Namespace, escape_backup: Path) -> int:
    """最小范围重建 → 健康等待（仅 api/livekit）→ 九键复核。

    任一失败打印确切回滚命令（绝不自动回滚——重建失败时容器状态需人工
    观察，自动叠加操作会放大风险）。
    """
    if not compose_up_limited(runner, log, COMPOSE_FILE, args.project,
                              args.profile, args.env_file):
        _say_rollback_hint(log, escape_backup)
        return EXIT_ERROR
    if not base.wait_stack_healthy(runner, log, COMPOSE_FILE, args.project,
                                   args.profile, set(REBUILD_SERVICES),
                                   args.health_wait_seconds):
        _say_rollback_hint(log, escape_backup)
        return EXIT_ERROR
    final = base.check_pins(args.env_file, runner, args.project, log)
    if not final.ok:
        log.say("复核: 九键 pin 不绿（键名见上）——env 与在线容器事实不一致")
        _say_rollback_hint(log, escape_backup)
        return EXIT_ERROR
    return EXIT_OK


# ---------------------------------------------------------------- 子命令

def run_plan(runner: base.Runner, log: base.RunLog,
             args: argparse.Namespace) -> int:
    """只读体检：九键 pin 状态 + 可选拓扑 delta 预览（零改动零重建）。"""
    log.say("=== LiveKit LAN cutover PLAN（全程只读，零改动） ===")
    if not base.wait_docker_engine(runner, log, args.engine_wait_seconds):
        return EXIT_ERROR
    report = base.check_pins(args.env_file, runner, args.project, log)
    if args.livekit_ip or args.public_url or args.bind_ip:
        env_values = base.parse_env_file(args.env_file)
        live = base.collect_live_pins(runner, args.project) or {}
        updates, error = resolve_updates(args, env_values, live)
        if error:
            log.say(f"参数: {error}")
            return EXIT_USAGE
        log.say("预览（零改动——执行需 apply --confirm-rebuild）:")
        _print_delta(log, env_values, updates)
    else:
        log.say("未提供拓扑参数——仅报告 pin 状态（plan --livekit-ip <LAN-IP> 可预览切换 delta）")
    if report.ok:
        log.say("=== 结果: OK（plan 只读完成） ===")
        return EXIT_OK
    log.say("=== 结果: FAIL（plan 只读完成，但当前 pin 未就绪——见上，仅报键名） ===")
    return EXIT_ERROR


def run_apply(runner: base.Runner, log: base.RunLog,
              args: argparse.Namespace) -> int:
    """切换到 LAN 拓扑：gate → 备份 → 原子更新 → 最小范围重建 → 九键复核。"""
    if args.confirm_rebuild != REBUILD_CONFIRM_TOKEN:
        log.say(f"拒绝: apply 需要 --confirm-rebuild {REBUILD_CONFIRM_TOKEN}（精确值）——零改动")
        return EXIT_USAGE
    log.say(f"=== LiveKit LAN cutover APPLY: project={args.project} env={args.env_file.name} ===")
    log.say(f"    重建范围: {'+'.join(REBUILD_SERVICES)}"
            "（依赖与其余服务绝不触碰；up --no-deps 幂等）")
    if not base.wait_docker_engine(runner, log, args.engine_wait_seconds):
        return EXIT_ERROR
    report = base.check_pins(args.env_file, runner, args.project, log)
    if not baseline_gate(report, log):
        return EXIT_ERROR
    env_values = base.parse_env_file(args.env_file)
    live = base.collect_live_pins(runner, args.project) or {}
    updates, error = resolve_updates(args, env_values, live)
    if error:
        log.say(f"参数: {error}")
        return EXIT_USAGE
    _print_delta(log, env_values, updates)
    backup = backup_env(args.env_file, log)
    try:
        atomic_update_env(args.env_file, updates)
        log.say(f"env 已原子更新（仅拓扑键: {', '.join(sorted(updates))}；secret 行原样保留）")
    except OSError as cause:
        log.say(f"env 原子更新失败（{type(cause).__name__}）——尝试恢复备份")
        _restore_or_report(args.env_file, backup, log)
        return EXIT_ERROR
    if not base.validate_compose(runner, log, COMPOSE_FILE, args.project,
                                 args.profile, args.env_file):
        log.say("compose config 校验失败——恢复备份，零重建")
        _restore_or_report(args.env_file, backup, log)
        return EXIT_ERROR
    outcome = _rebuild_and_verify(runner, log, args, backup)
    if outcome != EXIT_OK:
        return outcome
    log.say(f"=== 结果: OK（LAN cutover 完成；回滚锚点 {backup}）===")
    return EXIT_OK


def run_rollback(runner: base.Runner, log: base.RunLog,
                 args: argparse.Namespace) -> int:
    """从备份原子恢复 env + 最小范围重建（回滚前再备份当前，可再回滚）。

    完整性门在最前：伴生 sha256 校验失败即拒绝——此时尚未读备份 pin 值、
    未建 safety 备份、未调 Docker、未写 env（零副作用 fail-closed）。
    """
    if args.confirm_rebuild != REBUILD_CONFIRM_TOKEN:
        log.say(f"拒绝: rollback 需要 --confirm-rebuild {REBUILD_CONFIRM_TOKEN}（精确值）——零改动")
        return EXIT_USAGE
    log.say(f"=== LiveKit LAN cutover ROLLBACK: 备份={args.backup_file.name} ===")
    if not args.backup_file.is_file():
        log.say(f"rollback: 备份文件缺失（{args.backup_file}）——拒绝执行")
        return EXIT_ERROR
    if not verify_backup_checksum(args.backup_file, log):
        return EXIT_ERROR
    values = base.parse_env_file(args.backup_file)
    missing = tuple(key for key in base.PIN_KEYS if not values.get(key))
    if missing:
        log.say(f"rollback: 备份缺九键: {', '.join(missing)}——拒绝执行（值不回显）")
        return EXIT_ERROR
    placeholders = base.placeholder_pin_keys(values)
    if placeholders:
        log.say(f"rollback: 备份含模板占位值键: {', '.join(placeholders)}——拒绝执行")
        return EXIT_ERROR
    if not base.wait_docker_engine(runner, log, args.engine_wait_seconds):
        return EXIT_ERROR
    safety = backup_env(args.env_file, log)  # 回滚前快照（可再回滚）
    try:
        restore_from_backup(args.env_file, args.backup_file, log)
    except OSError as cause:
        log.say(f"env 恢复失败（{type(cause).__name__}）——人工核实（回滚前快照 {safety}）")
        return EXIT_ERROR
    if not base.validate_compose(runner, log, COMPOSE_FILE, args.project,
                                 args.profile, args.env_file):
        log.say("compose config 校验失败——当前 env 已是回滚内容，重建未执行；人工核实")
        _say_rollback_hint(log, safety)
        return EXIT_ERROR
    outcome = _rebuild_and_verify(runner, log, args, safety)
    if outcome != EXIT_OK:
        return outcome
    log.say(f"=== 结果: OK（回滚完成；回滚前快照 {safety}）===")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env_values = base.parse_env_file(args.env_file)
    redactions = {
        env_values[key]: f"***REDACTED:{key}***"
        for key in ("AIOS_AUTH_SECRET", "AIOS_LIVEKIT_API_SECRET")
        if env_values.get(key)
    }
    log = base.RunLog(redactions=redactions)
    runner = base.RealRunner()
    if args.command == "plan":
        outcome = run_plan(runner, log, args)
    elif args.command == "apply":
        outcome = run_apply(runner, log, args)
    else:
        outcome = run_rollback(runner, log, args)
    # plan 契约：真正零文件系统写——绝不建 ARTIFACT_DIR、绝不写日志文件
    # （stdout 照常）；--no-log-file 仅对 apply/rollback 有意义。
    if args.command != "plan" and not args.no_log_file:
        try:
            path = log.write_file(ARTIFACT_DIR)
            log.say(f"日志: {path}")
        except OSError as cause:
            log.say(f"日志写入失败（不影响结果）: {type(cause).__name__}")
    return outcome


if __name__ == "__main__":
    sys.exit(main())
