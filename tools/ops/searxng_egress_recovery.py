#!/usr/bin/env python
"""M14-148 SearXNG 出站恢复 helper：把部署代理槽位恢复到直连出网默认。

背景（M14-147 preflight 实证 + supervisor 诊断，2026-09-25）：生产
SearXNG 容器 healthy 但六个上游引擎全数 ``Suspended: timeout``——容器
出站被部署 env 的 ``AIOS_SEARXNG_HTTP(S)_PROXY`` 固定在一条**当前已坏**
的 SOCKS 代理路径上；宿主直连 egress（example.com / api.github.com /
cn.bing.com）正常。仓库契约（M14-66 修正轮）：compose 的
``HTTP_PROXY: ${AIOS_SEARXNG_HTTP_PROXY:-}`` 三槽位在键**缺省或为空**时
插值为空 = urllib getproxies 忽略空值 = **直连出网**。本工具把「恢复
到直连默认」从纯手工、不可审计的 env 文件编辑变成一条幂等、fail-closed、
零服务触碰的可审计路径：

- **validate key names only**：对 ``infra/env.production-recovery``
  （gitignored 部署 pin 事实源）只做**键名**验证——production_recovery
  的九个 PIN_KEYS（``tools/ops/production_recovery.py`` 经 importlib
  复用其常量，单一事实源，绝无第二份副本）必须全部以激活形态在位；
  缺任一键 → fail-closed 拒绝（仅报键名）。代理槽位值只做
  存在/非空布尔判定，**绝不读取进输出**（不回显任何 env 值）。
- **disable broken proxy slots**：把激活且非空的
  ``AIOS_SEARXNG_HTTP_PROXY`` / ``AIOS_SEARXNG_HTTPS_PROXY`` 行改为带
  固定标记的注释形态（``# [disabled-by-searxng-egress-recovery]
  KEY=…``——原值保留在 gitignored secret 文件内供日后 opt-in 取消
  注释，compose --env-file 解析跳过注释行 → 插值回空 = 直连）。
  ``AIOS_SEARXNG_NO_PROXY`` 不在禁用集：直连下无效但无害，最小变更
  不触碰。**幂等**：已是标记形态 / 值本就为空 / 键缺席 → no-op。
- **preserve PIN_KEYS exactly**：只改目标槽位行，其余行（含九键）逐
  字节原样写回；写后立即重新解析并重验九键齐全，验证失败 → 可见失败
  （exit 1）。本工具绝不新增/删除/改写任何 PIN_KEY 行，绝不把代理
  槽位并入 PIN_KEYS（代理槽位是随网络状况可变的开关键，不参与 pin
  一致性比对——那是 production_recovery 的九键契约，原样保持）。
- **严格 UTF-8 fail-closed（supervisor 修正 Round 1）**：env 文件
  读取恒严格解码——非 UTF-8 字节在任何写入之前可见拒绝（exit 1），
  不回显解码内容或替换字符；dry-run 与 enforce 两路径下文件字节均
  逐字节不变（本工具不做有损解码/改写）。
- **原子替换写（supervisor 修正 Round 1）**：enforce 恒经同目录临时
  文件原子替换（``_atomic_write_text``：mkstemp → 严格 UTF-8 写 +
  flush + fsync + close → 复制原文件权限位（平台支持时）→
  ``os.replace``）；任一步失败 → 临时文件清理、原文件逐字节不变、
  可见失败——secret 文件绝不承受半写状态。
- **dry-run plan**：``--dry-run`` 全程只读——输出将禁用的键名（含
  行号）+ 渲染语义预告 + 后续人工步骤（不在本工具范围），零写入。
- **零服务触碰（Round 1 硬边界）**：本工具**无任何子进程**（源码
  契约测试锁定：不 import subprocess / 不构造 docker / compose / 服务
  命令）——不 stop/restart/recreate 任何容器，不改在线容器 env；enforce
  只编辑 env 文件本身。使新 egress 事实生效所需的 searxng 容器
  recreate 是 **Round 2 由 supervisor 获准后的人工/受控动作**，本工具
  只在计划里以文字预告。

退出码：0 完成/无需变更（dry-run 计划成功同样 0）；1 可见失败
（env 文件缺失 / 非严格 UTF-8 / PIN_KEYS 缺键 / 原子替换失败 /
写后重验失败）；2 参数错误。

用法（仓库根）：
  python tools/ops/searxng_egress_recovery.py --dry-run   # 只读验证 + 计划
  python tools/ops/searxng_egress_recovery.py             # 禁用坏代理槽位（幂等）
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import stat
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / "infra" / "env.production-recovery"
PRODUCTION_RECOVERY_SCRIPT = REPO_ROOT / "tools" / "ops" / "production_recovery.py"
ENV_TEMPLATE = REPO_ROOT / "infra" / "env.production-recovery.example"

TAG = "[searxng-egress-recovery]"

#: 禁用目标槽位（键名是仓库公开 compose 契约，非 secret）。NO_PROXY 刻意
#: 不在禁用集——直连语义下它无效但无害，最小变更不触碰。
EGRESS_PROXY_SLOT_KEYS: tuple[str, ...] = (
    "AIOS_SEARXNG_HTTP_PROXY",
    "AIOS_SEARXNG_HTTPS_PROXY",
)

#: 禁用行固定标记前缀（幂等识别 + 可审计来源声明；原值保留其后的
#: gitignored secret 文件内，永不进入输出）
DISABLE_MARKER = "# [disabled-by-searxng-egress-recovery] "
#: 同标记剥去注释符后的主体形态（对已 lstrip("#") 的行做幂等识别用）
DISABLE_BODY_MARKER = "[disabled-by-searxng-egress-recovery]"


def _load_pin_keys() -> tuple[str, ...]:
    """importlib 复用 production_recovery.PIN_KEYS（单一事实源，零副本）。

    加载 production_recovery 模块不执行任何 Docker/服务命令（其副作用
    全部在 main()/run_recovery() 内，模块级只有常量与纯函数定义）。
    """
    spec = importlib.util.spec_from_file_location(
        "production_recovery_for_egress_recovery", PRODUCTION_RECOVERY_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return tuple(module.PIN_KEYS)  # type: ignore[attr-defined]


#: production_recovery 九键（M14-148 契约：exactly preserved，绝不改写）
PIN_KEYS: tuple[str, ...] = _load_pin_keys()


def parse_active_keys(lines: list[str]) -> dict[str, int]:
    """激活形态（行首无 ``#``）的 KEY= 行 → {键名: 0 起行号}。

    值不进入返回值——本工具对 env 文件的一切判定只基于键名与行的
    形态（激活/注释/空值），secret 值永不离开文件。
    """
    active: dict[str, int] = {}
    for index, raw in enumerate(lines):
        text = raw.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key = text.partition("=")[0].strip()
        active[key] = index
    return active


def _slot_value_empty(raw: str) -> bool:
    """槽位行值是否为空（仅布尔判定；值本身不返回、不输出）。"""
    text = raw.strip()
    value = text.partition("=")[2].strip()
    return not value


def _slot_key_of(raw: str) -> str | None:
    """任意形态的槽位行 → 其键名；非槽位行 → None（值不读不返回）。"""
    stripped = raw.strip()
    if stripped.startswith("#"):
        body = stripped.lstrip("#").strip()
        if body.startswith(DISABLE_BODY_MARKER):
            body = body[len(DISABLE_BODY_MARKER):].strip()
        for key in EGRESS_PROXY_SLOT_KEYS:
            if body.startswith(f"{key}="):
                return key
        return None
    for key in EGRESS_PROXY_SLOT_KEYS:
        if stripped.startswith(f"{key}="):
            return key
    return None


def classify_slot_line(raw: str) -> str:
    """把一行归类为代理槽位状态（固定词汇；不读值）。

    返回：``absent``（非本工具槽位）/ ``active``（激活且非空 = 走代理）/
    ``active-empty``（激活但空 = 已是直连默认，无需禁用）/
    ``disabled``（本工具标记形态 = 已禁用，幂等 no-op）/
    ``commented``（原生注释形态 = 未启用，= 直连默认）。
    """
    key = _slot_key_of(raw)
    if key is None:
        return "absent"
    stripped = raw.strip()
    if stripped.startswith("#"):
        body = stripped.lstrip("#").strip()
        if body.startswith(DISABLE_BODY_MARKER):
            return "disabled"
        return "commented"
    return "active-empty" if _slot_value_empty(stripped) else "active"


def plan_recovery(lines: list[str]) -> tuple[list[int], list[str], list[str]]:
    """扫描 env 文件行 → (待禁用行号, 已是直连默认的键, 已禁用的键)。

    只报告键名/行号/状态词汇——任何 env 值都不进入输出。
    """
    to_disable: list[int] = []
    already_direct: list[str] = []
    already_disabled: list[str] = []
    for index, raw in enumerate(lines):
        state = classify_slot_line(raw)
        key = _slot_key_of(raw)
        if key is None:
            continue
        if state == "active":
            to_disable.append(index)
        elif state == "active-empty":
            already_direct.append(key)
        elif state in ("disabled", "commented"):
            already_disabled.append(key)
    return to_disable, already_direct, already_disabled


def disable_lines(lines: list[str], to_disable: list[int]) -> list[str]:
    """把目标行替换为标记注释形态（保留原行尾；其余行逐字节不变）。

    原值保留在注释行内（gitignored secret 文件的既有内容不删——供日后
    opt-in 取消注释恢复代理）；输出/日志永不包含该行内容。
    """
    result = list(lines)
    for index in to_disable:
        raw = result[index]
        newline = "\r\n" if raw.endswith("\r\n") else ("\n" if raw.endswith("\n") else "")
        body = raw.strip()
        result[index] = f"{DISABLE_MARKER}{body}{newline}"
    return result


def _atomic_write_text(env_file: Path, content: str) -> None:
    """同目录临时文件原子替换写（严格 UTF-8；失败清理临时文件、原文件不动）。

    纪律（supervisor 修正 Round 1）：secret 文件绝不承受「半写状态」——
    直接 write_text 在写中途断电/崩溃会留下截断的 env 文件（部署 pin
    事实源被破坏）。写序：同目录 mkstemp（不跨卷，replace 原子）→
    严格 UTF-8 写入 + flush + fsync + close → 复制原文件权限位（平台
    支持时；不支持不阻断）→ ``os.replace`` 原子替换。任一步失败：
    临时文件被清理、原文件逐字节不变、异常向上传播由调用方可见失败。
    """
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            dir=str(env_file.parent), prefix=f"{env_file.name}.", suffix=".tmp"
        )
        temp_path = Path(temp_name)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp_path, stat.S_IMODE(env_file.stat().st_mode))
        except OSError:
            pass  # 权限位保留 where supported——平台不支持时不阻断
        os.replace(temp_path, env_file)
        temp_path = None  # 替换成功：temp 已被消费，无需清理
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass


def run_recovery(env_file: Path, *, dry_run: bool, say) -> int:
    """主流程：键名验证 → 计划 → （非 dry-run）禁用 + 写后重验。"""
    say(f"模式: {'DRY-RUN（全程只读，零写入）' if dry_run else 'ENFORCE（仅编辑 env 文件，零服务触碰）'}")
    say(f"env 文件: {env_file}")
    if not env_file.is_file():
        say(f"拒绝: env 文件缺失——fail-closed（模板 {ENV_TEMPLATE}）")
        return EXIT_ERROR
    try:
        text = env_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        say("拒绝: env 文件非严格 UTF-8——fail-closed（内容不回显；文件字节不变）")
        say("      请人工核查编码后重试（本工具不做有损解码/改写）")
        return EXIT_ERROR
    lines = text.splitlines(keepends=True)
    active = parse_active_keys(lines)
    missing = tuple(key for key in PIN_KEYS if key not in active)
    if missing:
        say(f"拒绝: PIN_KEYS 缺键（仅键名，值不回显）: {', '.join(missing)}")
        say("      production_recovery 九键契约必须原样在位——先修复 env 文件再恢复出站")
        return EXIT_ERROR
    say(f"pin: 九键齐全（键名验证: {', '.join(PIN_KEYS)}；值不读取不回显）")
    to_disable, already_direct, already_disabled = plan_recovery(lines)
    if not to_disable:
        say("代理槽位: 无激活且非空的代理槽位——已是直连出网默认，无需变更（幂等 no-op）")
        say("渲染语义: compose `${{{AIOS_SEARXNG_HTTP_PROXY}:-}}` 缺省/空 = 空 = 直连")
        return EXIT_OK
    for index in to_disable:
        key = lines[index].strip().partition("=")[0]
        say(f"计划禁用: {key}（第 {index + 1} 行，值非空——不回显）")
    if already_direct:
        say(f"已为空值（= 直连默认，不动）: {', '.join(sorted(already_direct))}")
    if already_disabled:
        say(f"已为禁用/注释形态（幂等）: {', '.join(sorted(set(already_disabled)))}")
    say("渲染语义: 禁用后 compose 渲染 HTTP_PROXY/HTTPS_PROXY 为空 = urllib 忽略 = 直连出网（生产可恢复默认）")
    say("PIN_KEYS 保持: 九键行不触碰（本工具绝不改写 pin 键行；代理槽位不参与 pin 比对）")
    say("后续（不在本工具范围，需 supervisor 获准）: recreate searxng 容器使新 egress 事实生效；")
    say("      再以 provider-smoke-preflight / provider-smoke-export 复核 search 槽位")
    if dry_run:
        say("DRY-RUN: 零写入——enforce 将仅注释上述槽位行（幂等，值保留在文件内不回显）")
        return EXIT_OK
    new_text = "".join(disable_lines(lines, to_disable))
    try:
        _atomic_write_text(env_file, new_text)
    except OSError as cause:
        say(f"写入失败: 原子替换未完成（{type(cause).__name__}）——原文件未动、临时文件已清理，可见失败")
        return EXIT_ERROR
    try:
        after = env_file.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        say("写后重验失败: 文件读取非严格 UTF-8（可能被并发外部修改）——可见失败，请人工核查")
        return EXIT_ERROR
    after_active = parse_active_keys(after)
    after_missing = tuple(key for key in PIN_KEYS if key not in after_active)
    if after_missing:
        say(f"写后重验失败: PIN_KEYS 缺键 {', '.join(after_missing)}——可见失败（请人工核查 env 文件）")
        return EXIT_ERROR
    still_active = [
        raw.strip().partition("=")[0]
        for raw in after
        if classify_slot_line(raw) == "active"
    ]
    if still_active:
        say(f"写后重验失败: 仍有激活代理槽位 {', '.join(still_active)}——可见失败")
        return EXIT_ERROR
    say(f"已禁用 {len(to_disable)} 个代理槽位（行内容不回显）；PIN_KEYS 九键写后重验齐全")
    say("结果: OK——部署 env 已恢复直连出网默认（容器 egress 事实待 Round 2 获准 recreate）")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="searxng_egress_recovery.py",
        description="SearXNG 出站恢复：把部署 env 的代理槽位恢复到直连出网默认"
                    "（幂等、fail-closed、零服务触碰；PIN_KEYS 九键原样保持）",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="全程只读：验证 + 禁用计划预告，零写入")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE,
                        help=f"部署 pin env 文件（默认 {DEFAULT_ENV_FILE}；模板 .example）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def say(message: str) -> None:
        print(f"{TAG} {message}", flush=True)

    return run_recovery(args.env_file, dry_run=args.dry_run, say=say)


if __name__ == "__main__":
    sys.exit(main())
