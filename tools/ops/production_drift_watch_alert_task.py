#!/usr/bin/env python
"""M14-137 production drift watch 告警分发调度桥 readiness 工具。

补「既有 production drift-watch 报告（M14-127 产出、M14-129 计划任务
自然累积）与 M14-135 告警分发 CLI 之间的工具级缺口」：从 drift-watch
工件目录**fail-closed 选中唯一最新**合法 execute 模式报告，plan 报告
会否需要分发；execute 把分发**原样移交**既有 M14-135 dispatch CLI
（其全部门禁/幂等/脱敏语义零削弱、零复制）。**本切片只交付工具
readiness**：零计划任务安装/改动、零生产执行、零真实 webhook 外发
（真实外发仅由 supervisor 在获准窗口经 M14-135 进行）。

设计纪律（与 M14-129/M14-135 同源的已验证模式）：
- **默认 plan（只读、零网络、零子进程、零写入）**：仅读取并严格校验
  报告，stdout 报告结论；本工具自身**绝不构造任何 Transport**、绝不
  读写 M14-135 台账、绝不落盘任何文件（plan 成功与否都不写工件）。
- **报告校验单一事实源复用（绝不发明平行 schema）**：单报告读取与
  schema/自洽校验直接复用 M14-135 ``load_drift_report`` /
  ``validate_drift_report``（同一实现对象，契约测试 ``is`` 锁定），
  目录路径安全复用 M14-133 ``reject_path_problems`` 语义；文件名
  白名单/时间戳容差/drift_reasons 词汇等全部沿用既有常量。
- **最新报告选择（fail-closed）**：仅接受文件名严格匹配
  ``drift-watch-YYYYMMDD-HHMMSS.json`` 的候选（伴生 .md/plan-* 忽略
  只计数）；任一候选文件名时间戳非真实日历时刻（无法定序）→ 拒绝；
  **最新候选必须完整通过 M14-135 校验**——malformed/plan 模式报告/
  任何校验违规 → 可见拒绝且零移交，**绝不回退到更旧报告**（过期
  drift 结论绝不冒充最新证据）；目录缺失/空/零候选/不可读（枚举
  OSError/权限 → ``artifacts-dir-unreadable``，异常文本/本地路径
  零外泄）→ 拒绝。
  ``--report`` 精确指定单份报告时跳过扫描（与 ``--artifacts-dir``
  显式同给 → 拒绝，绝不猜来源）。
- **drift 判定只依据报告**（绝不重判 Docker 状态）：drift=false →
  exit 0 + 显式 skipped-no-alerts（零网络、零台账变更；execute 亦
  **不构造** dispatch 子进程）；plan 且 drift=true → exit 3 +
  stdout 报告**精确选中报告 SHA-256**与「需要分发」结论（零网络）。
- **execute 三重门禁（先于一切读取与子进程构造）**：``--execute`` +
  ``--confirm "EXECUTE PRODUCTION DRIFT WATCH ALERT TASK"``（一字
  不差；与 M14-127/M14-129/M14-135 既有短语互不通用）+
  ``--secret-file``（本地仅查存在性，内容不读取不回显——校验留给
  M14-135）。缺一/近似 → exit 2 且零子进程构造。
- **移交 = 既有 M14-135 CLI + 其既有门禁**：唯一放行的子进程形态经
  **结构性白名单门**（GatedDispatchRunner）逐 token 校验——
  ``<sys.executable> <repo>/tools/ops/production_drift_watch_alert_dispatch.py
  --report <选中报告> --secret-file <操作者文件> [--artifact-dir <dir>]
  --execute --confirm "EXECUTE PRODUCTION DRIFT WATCH ALERT DISPATCH"``
  （确认短语是 M14-135 自有常量，非本工具短语）；其余任何形态在任何
  执行之前拒绝。子进程退出码原样透传（成功 0 / 一切拒绝 2）；幂等
  （同报告 SHA-256 重复分发拒绝）与 sanitized 台账全部由 M14-135
  既有语义承担——本工具零复制、零削弱、零旁路。
- **脱敏**：stdout 恒经 ``redact_secrets``（M14-119 同一实现）防御性
  终防线；子进程 stdout/stderr 逐行经同通道回显；日志只出现 stem/
  sha256/计数/固定词汇类别，**绝不出现 URL/token/绝对本地路径**。
- secret 纪律：零 secret 产生/落盘/传输/回显；本工具零 env 读取、
  零 Docker、零调度器改动、零网络代码（网络仅存在于被移交的
  M14-135 子进程内部）。

退出码：0 plan/execute 无需分发（skipped-no-alerts）或 execute 移交
成功；3 plan 且 drift=true（需要分发但零网络零移交）；2 一切
fail-closed 拒绝（门禁/路径/选择/校验失败、子进程不可执行/超时）与
M14-135 移交拒绝（透传，含 duplicate-dispatch 与分发失败）。

用法（仓库根）：
  python tools/ops/production_drift_watch_alert_task.py                     # plan：默认工件目录
  python tools/ops/production_drift_watch_alert_task.py --report <drift-watch-*.json>
  python tools/ops/production_drift_watch_alert_task.py --secret-file <s.json> \
      --execute --confirm "EXECUTE PRODUCTION DRIFT WATCH ALERT TASK"
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

try:  # 直接脚本运行：tools/ops 自身在 sys.path
    import production_drift_watch_alert_dispatch as dispatch
    import production_drift_watch_history as history
except ImportError:  # 经 importlib 按路径加载（契约测试形态）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import production_drift_watch_alert_dispatch as dispatch
    import production_drift_watch_history as history

OPS_DIR = Path(__file__).resolve().parent
REPO_ROOT = OPS_DIR.parent.parent

TOOL_NAME = "tools/ops/production_drift_watch_alert_task.py"
MILESTONE = "M14-137"
TAG = "[drift-watch-alert-task]"
#: 本工具（任务桥）execute 确认短语——与 M14-127 watcher / M14-129
#: scheduler / M14-135 dispatcher 的既有短语互不通用（契约测试交叉 pin）
TASK_CONFIRM_PHRASE = "EXECUTE PRODUCTION DRIFT WATCH ALERT TASK"
#: 移交目标：既有 M14-135 dispatch CLI（唯一允许的子进程目标）
DISPATCH_TOOL = OPS_DIR / "production_drift_watch_alert_dispatch.py"
#: 默认 drift-watch 工件目录 = M14-127 canonical 目录（经 M14-133 单一
#: 事实源常量复用，绝不另立平行默认）
DEFAULT_ARTIFACTS_DIR = history.DEFAULT_INPUT_DIR

EXIT_OK = 0
#: 2 = 一切 fail-closed 拒绝（与 drift-watch 家族统一可见拒绝口径；
#: 亦为 M14-135 移交拒绝的透传码）
EXIT_REFUSED = 2
#: 3 = plan 且 drift=true：需要分发（只报告，零网络零移交）——与
#: 拒绝（2）严格区分的正向发现信号，Task Scheduler「上次运行结果」可见
EXIT_DISPATCH_REQUIRED = 3

#: dispatch 子进程墙钟预算：M14-135 单次 POST 超时上界 30s（M14-119
#: MAX_TIMEOUT_SECONDS）+ 解释器启动/报告校验/工件落盘余量 → 300s 足够
#: 且绝不放纵悬挂（超时按 RunnerError fail-closed）
CHILD_TIMEOUT_SECONDS = 300.0

#: 单报告读取 + schema 校验单一事实源（M14-135 同一实现对象，非平行副本）
load_drift_report = dispatch.load_drift_report
validate_drift_report = dispatch.validate_drift_report
#: 目录路径安全（traversal/symlink 组件/存在性）单一事实源（M14-133）
reject_path_problems = history.reject_path_problems
#: 文件名白名单 + stamp 形态（M14-135 常量，同 M14-133 同值）
SOURCE_FILE_NAME_RE = dispatch.SOURCE_FILE_NAME_RE
FILE_STAMP_FORMAT = dispatch.FILE_STAMP_FORMAT
#: stdout 防御性脱敏终防线（M14-119 同一实现）
redact_secrets = dispatch.redact_secrets


class TaskRefused(RuntimeError):
    """fail-closed 拒绝（固定词汇类别；绝不携带 URL/token/路径文本）。"""


class SafeLog:
    """行式日志：stdout 输出前经 redact_secrets 防御性脱敏终防线。"""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def say(self, message: str) -> None:
        line = redact_secrets(message)
        self.lines.append(line)
        print(f"{TAG} {line}", flush=True)


# ---------------------------------------------------------------- Runner + 白名单门


class RunnerError(RuntimeError):
    """子进程执行失败（不可执行/超时）——类别化处理，绝不保留文本。"""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def os_windows() -> bool:
    return os.name == "nt"


class RealRunner:
    """真实子进程执行：list-argv（无 shell）、capture、Windows 侧恒
    CREATE_NO_WINDOW。本工具唯一的 subprocess 触达点。"""

    def run(self, argv: tuple[str, ...] | list[str], *,
            timeout: float = CHILD_TIMEOUT_SECONDS) -> CommandResult:
        text_argv = [str(item) for item in argv]
        kwargs: dict[str, object] = {"capture_output": True, "text": True,
                                     "encoding": "utf-8", "errors": "replace"}
        if os_windows():
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        try:
            result = subprocess.run(text_argv, check=False, timeout=timeout,
                                    **kwargs)  # type: ignore[arg-type]
        except (OSError, subprocess.TimeoutExpired) as cause:
            raise RunnerError("dispatch-subprocess-unavailable") from cause
        return CommandResult(tuple(text_argv), result.returncode,
                             result.stdout or "", result.stderr or "")


def is_allowed_dispatch_argv(argv: tuple[str, ...] | list[str]) -> bool:
    """结构性白名单：唯一放行「移交 M14-135 CLI」的精确 argv 形态。

    两种长度（无/有可选 ``--artifact-dir <dir>`` 对）：

    - 9: ``<python> <dispatch.py> --report <p> --secret-file <p>
      --execute --confirm "<M14-135 短语>"``
    - 11: 同上，在 ``--secret-file`` 值后插入 ``--artifact-dir <p>``

    逐 token 校验：python 必须是 ``sys.executable``（调用方自身解释
    器）；工具路径必须精确等于仓库内 M14-135 CLI；旗标序列/顺序精确；
    值位置非空且不以 ``-`` 起头（盘符绝对路径 ``D:\\...`` 与 POSIX
    绝对路径 ``/tmp/...`` 都是路径值，合法——同 M14-129 R3 实证先例，
    靠结构而非首字符黑名单；M14-135 旗标全部 ``--`` 前缀，值位置的
    ``-x`` token 保守拒绝）；末 token 必须一字不差等于 M14-135 自有
    确认短语。其余任何形态（其它工具、缺 --execute、近似短语、追加
    旗标、多余 token）一律 False。"""
    tokens = tuple(str(item) for item in argv)
    if len(tokens) not in (9, 11):
        return False
    if tokens[0] != sys.executable or tokens[1] != str(DISPATCH_TOOL):
        return False
    if tokens[2] != "--report" or tokens[4] != "--secret-file":
        return False
    if _is_flag_shaped(tokens[3]) or _is_flag_shaped(tokens[5]):
        return False
    index = 6
    if len(tokens) == 11:
        if tokens[6] != "--artifact-dir" or _is_flag_shaped(tokens[7]):
            return False
        index = 8
    if tokens[index] != "--execute" or tokens[index + 1] != "--confirm":
        return False
    return tokens[index + 2] == dispatch.CONFIRM_PHRASE


def _is_flag_shaped(value: str) -> bool:
    """值位置不得为空或以 ``-`` 起头（保守旗标形态拒绝）；盘符与 POSIX
    绝对路径（``D:\\``、``/tmp/``）是路径值，恒合法。"""
    return value == "" or value.startswith("-")


class GatedDispatchRunner:
    """白名单门装饰器：非「精确移交 M14-135 CLI」形态在任何执行之前
    拒绝（fail-closed）；plan/拒绝路径恒零构造。"""

    def __init__(self, inner: RealRunner) -> None:
        self._inner = inner

    def run(self, argv: tuple[str, ...] | list[str], *,
            timeout: float = CHILD_TIMEOUT_SECONDS) -> CommandResult:
        if not is_allowed_dispatch_argv(argv):
            raise RunnerError("dispatch-argv-not-whitelisted")
        return self._inner.run(argv, timeout=timeout)


# ---------------------------------------------------------------- 报告选择（fail-closed）


@dataclass(frozen=True)
class Selection:
    """选中的权威报告（stem/sha256/摘要；path 仅内部使用绝不打印）。"""

    stem: str
    stamp: str
    sha256: str
    summary: dict[str, object]
    path: Path


def _load_and_validate(store: dispatch.Store, path: Path) -> Selection:
    """单一事实源复用：M14-135 读取+校验（文件名白名单/symlink/大小/
    JSON/身份/mode/时间戳交叉/config/counts/drift 自洽/词汇）。"""
    loaded, report_error = load_drift_report(store, path)
    if report_error is not None:
        raise TaskRefused(report_error)
    assert loaded is not None
    stem, stamp, report_sha256, data = loaded
    try:
        summary = validate_drift_report(stamp, data)
    except dispatch.AlertDispatchError as cause:
        if str(cause) == "report-mode":
            raise TaskRefused("report-mode") from None
        raise TaskRefused(str(cause)) from None
    return Selection(stem=stem, stamp=stamp, sha256=report_sha256,
                     summary=summary, path=path)


def scan_candidates(artifacts_dir: Path) -> tuple[list[tuple[str, str]], int]:
    """只读枚举候选（文件名白名单）；返回 ((文件名, stamp) 升序, 忽略数)。
    任一候选 stamp 非真实日历时刻 → 拒绝（无法定序，绝不猜）；枚举本身
    失败（``os.scandir`` OSError/PermissionError）→ 固定类别
    ``artifacts-dir-unreadable`` 拒绝——异常文本/路径绝不外泄。"""
    candidates: list[tuple[str, str]] = []
    ignored = 0
    try:
        with os.scandir(artifacts_dir) as entries:
            scanned = sorted(entries, key=lambda item: item.name)
    except OSError:
        raise TaskRefused("artifacts-dir-unreadable") from None
    for entry in scanned:
        match = SOURCE_FILE_NAME_RE.match(entry.name)
        if match is None:
            ignored += 1
            continue
        stamp = match.group(1)
        try:
            datetime.strptime(stamp, FILE_STAMP_FORMAT).replace(
                tzinfo=timezone.utc)
        except ValueError:
            raise TaskRefused("candidate-filename-timestamp-invalid") from None
        candidates.append((entry.name, stamp))
    return candidates, ignored


def select_report(store: dispatch.Store, *, report: Path | None,
                  artifacts_dir: Path | None) -> tuple[Selection, str, int]:
    """解析输入源 → 唯一选中报告。返回 (selection, 模式说明, 候选数)。

    - ``--report`` 精确指定：拒绝 ``..`` 组件后直接复用 M14-135 校验
      （symlink/大小/JSON/schema 由其承担）。
    - 目录模式：M14-133 路径安全（traversal/symlink 组件/存在目录）
      + 候选枚举 + **最新候选必须完整通过校验**（绝不回退更旧报告）。
    """
    if report is not None:
        for part in report.parts:
            if part == "..":
                raise TaskRefused("path-traversal")
        return _load_and_validate(store, report), "exact", 1
    directory = artifacts_dir if artifacts_dir is not None else DEFAULT_ARTIFACTS_DIR
    try:
        reject_path_problems(directory, must_exist_as_dir=True)
    except history.HistoryError as cause:
        raise TaskRefused(f"artifacts-dir-{cause}") from None
    candidates, _ignored = scan_candidates(directory)
    if not candidates:
        raise TaskRefused("no-drift-watch-reports")
    newest_name, _newest_stamp = max(candidates, key=lambda item: item[1])
    selection = _load_and_validate(store, directory / newest_name)
    return selection, "newest", len(candidates)


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="production_drift_watch_alert_task.py",
        description="M14-137 production drift watch 告警分发调度桥 readiness 工具："
                    "从 drift-watch 工件目录 fail-closed 选中唯一最新合法 execute 模式"
                    "报告（校验单一事实源复用 M14-135），默认 plan 只读零网络零写入；"
                    "execute 三重门禁后把分发原样移交既有 M14-135 dispatch CLI"
                    "（结构性白名单 argv 门 + 退出码透传；幂等/脱敏语义零削弱零复制）；"
                    "本切片零调度器安装/改动、零生产执行、零真实 webhook",
    )
    parser.add_argument("--artifacts-dir", type=Path, default=None,
                        help="drift-watch 报告目录（默认 .verify/artifacts/"
                             "m14-127-production-drift-watch，M14-133 单一事实源常量；"
                             "与 --report 显式同给则拒绝）")
    parser.add_argument("--report", type=Path, default=None,
                        help="精确指定单份 drift-watch-YYYYMMDD-HHMMSS.json"
                             "（跳过目录扫描；仍须完整通过 M14-135 校验）")
    parser.add_argument("--secret-file", type=Path, default=None,
                        help="操作者 webhook secret JSON 文件（仅 execute 读取存在性；"
                             "内容校验/回显禁止全部由 M14-135 承担）")
    parser.add_argument("--execute", action="store_true",
                        help="真实移交分发（默认 plan：零网络、零子进程、零写入）")
    parser.add_argument("--confirm", default="",
                        help=f'execute 必配 --confirm "{TASK_CONFIRM_PHRASE}"（精确匹配）')
    parser.add_argument("--dispatch-artifact-dir", type=Path, default=None,
                        help="可选：透传 M14-135 --artifact-dir（分发报告/台账目录；"
                             "默认不透传即 M14-135 自身默认）")
    return parser


def _spawn_dispatch(runner, selection: Selection, secret_file: Path,
                    dispatch_artifact_dir: Path | None, log: SafeLog) -> int:
    """构造白名单 argv → 移交 M14-135 CLI → 退出码透传（输出逐行脱敏回显）。"""
    argv: list[str] = [sys.executable, str(DISPATCH_TOOL),
                       "--report", str(selection.path),
                       "--secret-file", str(secret_file)]
    if dispatch_artifact_dir is not None:
        argv += ["--artifact-dir", str(dispatch_artifact_dir)]
    argv += ["--execute", "--confirm", dispatch.CONFIRM_PHRASE]
    try:
        result = runner.run(argv)
    except RunnerError as cause:
        log.say(f"拒绝: dispatch 移交失败（{cause}）——零弱化零旁路")
        return EXIT_REFUSED
    for line in (result.stdout or "").splitlines():
        if line.strip():
            log.say(redact_secrets(line))  # 预脱敏（不依赖 log 汇自身）
    for line in (result.stderr or "").splitlines():
        if line.strip():
            log.say(f"[child-stderr] {redact_secrets(line)}")
    return result.returncode


def main(argv: list[str] | None = None, *, runner=None, log=None) -> int:
    args = build_parser().parse_args(argv)
    if log is None:
        log = SafeLog()
    store = dispatch.RealStore()

    # 1) 输入源互斥（绝不猜来源）+ plan 不接受 secret（plan 不读取，
    #    提供即拒——防「以为 plan 已校验过 secret」的错觉）
    if args.report is not None and args.artifacts_dir is not None:
        log.say("拒绝: --report 与 --artifacts-dir 互斥（显式同给）——零选择")
        return EXIT_REFUSED
    if args.secret_file is not None and not args.execute:
        log.say("拒绝: --secret-file 仅 execute 使用（plan 零 secret 读取）")
        return EXIT_REFUSED

    # 2) execute 三重门禁（先于一切报告读取与子进程构造；零弱化）
    if args.execute:
        if args.confirm != TASK_CONFIRM_PHRASE:
            log.say(f'拒绝: --execute 必配 --confirm "{TASK_CONFIRM_PHRASE}"'
                    "（精确匹配，当前不匹配）——零移交（fail-closed）")
            return EXIT_REFUSED
        if args.secret_file is None:
            log.say("拒绝: execute 需 --secret-file（操作者提供的 webhook secret"
                    " JSON；内容校验由 M14-135 承担）——零移交")
            return EXIT_REFUSED
        if not args.secret_file.is_file():
            log.say("拒绝: secret-file-missing（本地存在性预检；内容不读取）——零移交")
            return EXIT_REFUSED

    # 3) 报告选择 + 严格校验（单一事实源复用；fail-closed；绝不重判漂移）
    try:
        selection, mode, candidate_count = select_report(
            store, report=args.report, artifacts_dir=args.artifacts_dir)
    except TaskRefused as cause:
        reason = str(cause)
        if reason == "report-mode":
            log.say("拒绝: 最新报告 mode 非 execute（plan 报告无权威 drift 结论"
                    "——绝不猜测，绝不回退更旧报告，零移交）")
        elif reason == "artifacts-dir-path-traversal":
            log.say("拒绝: 工件目录路径非法（path-traversal——含 .. 组件）——零移交")
        elif reason == "artifacts-dir-unreadable":
            log.say("拒绝: 工件目录不可读（artifacts-dir-unreadable）——零移交")
        elif reason.startswith("artifacts-dir-"):
            log.say(f"拒绝: 工件目录不可用（{reason}）——零移交")
        else:
            log.say(f"拒绝: 报告选择/校验失败（{reason}）——零移交"
                    "（绝不回退更旧报告）")
        return EXIT_REFUSED
    summary = selection.summary
    counts = summary["counts"]
    assert isinstance(counts, dict)
    drift = bool(summary["drift"])
    log.say(f"输入模式: {mode}（候选 {candidate_count} 份）")
    log.say(f"选中报告: {selection.stem}（sha256 {selection.sha256}；"
            f"drift={str(drift).lower()} pass={counts['pass']} fail={counts['fail']}）")

    # 4) drift=false → 无告警（成功路径；零网络、零台账变更；execute 亦零子进程）
    if not drift:
        log.say("结果: skipped-no-alerts（权威报告 drift=false——零网络、零台账写入"
                + ("、零 dispatch 子进程" if args.execute else "") + "）")
        return EXIT_OK

    # 5) plan 且 drift=true：报告精确 hash + 需要分发（零网络零移交）
    if not args.execute:
        log.say(f"dispatch-would-be-required: drift=true 需经 M14-135 dispatch CLI "
                f"外发（选中报告 sha256 {selection.sha256}；本 plan 零网络、零移交、"
                "secret 未读取）")
        log.say(f'execute 需: --secret-file <s.json> --execute --confirm "{TASK_CONFIRM_PHRASE}"'
                "（移交后仍受 M14-135 全部门禁/幂等/脱敏约束）")
        return EXIT_DISPATCH_REQUIRED

    # 6) execute 且 drift=true → 原样移交既有 M14-135 CLI（结构性白名单门）
    execute_runner = runner if runner is not None else GatedDispatchRunner(RealRunner())
    log.say(f"移交: M14-135 dispatch CLI（报告 {selection.stem}；其退出码原样透传，"
            "幂等/脱敏语义零削弱零旁路）")
    return _spawn_dispatch(execute_runner, selection, args.secret_file,
                           args.dispatch_artifact_dir, log)


if __name__ == "__main__":
    sys.exit(main())
