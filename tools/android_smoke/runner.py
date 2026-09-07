"""Android 冒烟 runner —— M12-06 完整编排（4a/4b/4c 小步）。

在安全基础层（配置校验 / 输出目录策略 / 原子写）与核心编排之上，
本模块还提供：

- :func:`parse_mock_log` / :func:`assert_mock_contract`：mock_requests.log
  JSONL 解析与只读契约断言（expected/unexpected/total 统计）；
- 搜索 / 治理两段真实 UI 流程（全部通过注入的 ``ui`` 驱动）；
- :func:`sha256_file`：APK 摘要；
- :func:`main`：真实 CLI 入口（仍只组装依赖，单测注入 fake 验证）。

本模块自身不访问真机、不启动真实 server；所有设备/网络交互都通过
注入的 ``adb`` / ``ui`` / ``server_factory`` 完成。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from tools.android_smoke.adb import AdbClient
from tools.android_smoke.interaction import AndroidUiController
from tools.android_smoke.logcat_analysis import analyze_logcat
from tools.android_smoke.summary import build_summary

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_OUTPUT = ".verify/android-smoke"
OUTPUT_PARENT = ".verify"

# host 只允许回环；mock server 永远只绑定 127.0.0.1
ALLOWED_HOSTS = frozenset({"127.0.0.1"})

PACKAGE_NAME = "com.ailearningos.app"
ACTIVITY_NAME = ".MainActivity"

# 首页就绪判定：等待 UI 文本 与 超时秒数
HOME_TEXT = "首页"
HOME_WAIT_TIMEOUT = 60.0

# 阶段产物名使用 ASCII key，界面点击文本见 TAB_STAGES
TAB_STAGES = (
    ("tab-home", "首页"),
    ("tab-study", "学习"),
    ("tab-search", "搜索"),
    ("tab-voice", "语音"),
    ("tab-settings", "设置"),
)

MOCK_REQUESTS_LOG = "mock_requests.log"
LOGCAT_FULL = "logcat-full.txt"
SUMMARY_JSON = "summary.json"

# 通用 UI 等待超时（秒）；wait_for 以第二个位置参数传入，
# 兼容 AndroidUiController.wait_for(text, timeout_seconds)。
STAGE_WAIT_TIMEOUT = 30.0

# ---------- 搜索 UI 流程文案与输入 ----------

SEARCH_TAB_TEXT = "搜索"
SEARCH_PROVIDERS_TEXT = "搜索源"
SEARCH_INPUT_HINT = "如：2024 清华大学 高等数学 选择题"
SEARCH_QUERY = "2024 tsinghua advanced math mcq"
PREVIEW_PLAN_TEXT = "预览计划"
PLAN_PREVIEW_TEXT = "查询计划（预览，未执行）"
SEARCH_RUN_TEXT = "搜索"
SEARCH_SUMMARY_TEXT = "搜索结果概要"
SEARCH_QUERY_ID_TEXT = "#9001"

# 回查区域是独立输入框（搜索后原输入框已有值，旧 placeholder 消失）；
# 搜索后输入框仍持有焦点、软键盘打开，直接 swipe 会落在输入法区域被当成
# 手势输入——先 back() 收起键盘，再连续两次向上滚动到回查区域。
LOOKUP_INPUT_HINT = "输入查询编号，如 42"
LOOKUP_SCROLL_ARGS = (540, 1800, 540, 600, 400)
# back() 收起 IME 后键盘收起动画未完成，立即 swipe 会被部分消费；
# 等待 0.8s 确保 IME 完全收起再开始滚动（r5 证据：首次 swipe 被吞，
# 页面只滚到 PlanCard 底部）。
LOOKUP_KEYBOARD_DISMISS_SECONDS = 0.8
# 第一次 swipe 后 LazyColumn 仍在滚动/settle，立即第二次 swipe 会被动画
# 吞掉；间隔 0.8s 再滚第二次（手工验证有效）。
LOOKUP_SCROLL_SETTLE_SECONDS = 0.8
LOOKUP_QUERY = "9001"
LOOKUP_RUN_TEXT = "回查记录"
LOOKUP_RESULT_TEXT = "原查询词"

# ---------- 治理只读流程文案 ----------

GOVERNANCE_HOME_TEXT = "首页"
GOVERNANCE_ENTRY_TEXT = "治理 / 发布"
GOVERNANCE_SECTIONS = ("发布版本", "运行快照", "审计留痕")
# 治理页可滚动：发布版本 / 运行快照首屏可见，审计留痕在下方；等前两个
# 区块出现后向上滚动一次让审计留痕进入视口（r7 证据：手工
# `input swipe 540 1800 540 600 400` 后审计留痕 bounds 可见）。
# 与 LOOKUP_SCROLL_ARGS 语义不同（回查滚动 vs 治理滚动），不复用。
GOVERNANCE_AUDIT_SECTION = "审计留痕"
GOVERNANCE_AUDIT_SCROLL_ARGS = (540, 1800, 540, 600, 400)

# ---------- mock 请求契约（镜像 mock_contract.ReadOnlyMockContract 路由表） ----------

EXPECTED_ENDPOINTS = frozenset(
    {
        ("GET", "/health"),
        ("GET", "/api/v1/auth/status"),
        ("GET", "/api/v1/system/privacy"),
        ("GET", "/api/v1/search/providers"),
        ("POST", "/api/v1/search/plan"),
        ("POST", "/api/v1/search/queries"),
        ("GET", "/api/v1/search/queries/9001"),
        ("GET", "/api/v1/version"),
        ("GET", "/api/v1/system/ops-snapshot"),
        ("GET", "/api/v1/audit"),
        # Tab 巡检触发的只读生命周期预取：允许 mock 返回 404（"功能不可用"
        # 属预期路径），契约只验证无写请求、无越权请求。
        ("GET", "/api/v1/papers"),
        ("GET", "/api/v1/voice/providers"),
    }
)
AUDIT_PATH = "/api/v1/audit"
AUDIT_REQUIRED_QUERY_KEYS = ["limit"]

# mock_requests.log 每行必须且只能含这些字段
REQUIRED_LOG_FIELDS = ("timestamp_utc", "method", "path", "query_keys", "body_len")

# LoopbackMockServer 在 server.py 中实现；真实 CLI 惰性加载，
# 测试可 monkeypatch 本模块级名字注入 fake。
LoopbackMockServer = None


class RunnerError(Exception):
    """配置或输出目录层面的错误（设备/网络执行错误不属于此类）。"""


@dataclass(frozen=True)
class RunnerConfig:
    """一次冒烟运行的配置；构造时即完成全部静态校验。

    ``apk`` 在 ``skip_install=True`` 时可为 ``None``；否则必须指向已存在
    的普通文件。
    """

    serial: str
    apk: Optional[str]
    output: Optional[str]
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    skip_install: bool = False
    keep_output: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.serial, str) or not self.serial.strip():
            raise RunnerError("serial is required and must be non-empty")
        if self.host not in ALLOWED_HOSTS:
            raise RunnerError(
                "host must be one of %s, got %r" % (sorted(ALLOWED_HOSTS), self.host)
            )
        if not isinstance(self.port, int) or isinstance(self.port, bool):
            raise RunnerError("port must be an integer, got %r" % (self.port,))
        if not 1 <= self.port <= 65535:
            raise RunnerError("port must be in 1..65535, got %d" % self.port)
        if self.apk is None:
            if not self.skip_install:
                raise RunnerError("apk is required unless --skip-install is set")
        else:
            apk_path = Path(self.apk)
            if not apk_path.is_file():
                raise RunnerError("apk does not exist or is not a regular file: %s" % self.apk)


def build_config(argv: Optional[Sequence[str]] = None) -> RunnerConfig:
    """解析命令行参数并构造 :class:`RunnerConfig`；非法参数抛 :class:`RunnerError`。"""
    parser = argparse.ArgumentParser(
        prog="android-smoke", description="Android 冒烟回归 runner（安全基础层）"
    )
    parser.add_argument("--serial", default=None, help="adb 设备序列号（必填）")
    parser.add_argument("--apk", default=None, help="待安装 APK 路径")
    parser.add_argument(
        "--output",
        default=None,
        help="显式输出目录（必须不存在；默认 .verify 下自动生成）",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="mock 服务器主机（仅限 127.0.0.1）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="mock 服务器端口")
    parser.add_argument("--skip-install", action="store_true", help="跳过 APK 安装")
    parser.add_argument("--keep-output", action="store_true", help="绝不清理输出目录")
    args = parser.parse_args(argv)

    if args.serial is None or not args.serial.strip():
        raise RunnerError("--serial is required and must be non-empty")
    return RunnerConfig(
        serial=args.serial,
        apk=args.apk,
        output=args.output,
        host=args.host,
        port=args.port,
        skip_install=args.skip_install,
        keep_output=args.keep_output,
    )


def _normalized(path: Path) -> Path:
    """绝对化并 normpath，但不解析 symlink。"""
    return Path(os.path.normpath(os.path.abspath(str(path))))


def _reject_symlinks(path: Path) -> None:
    """路径任意已存在组件含 symlink 即拒绝（resolve 后与 normpath 结果不一致）。"""
    normalized = _normalized(path)
    try:
        resolved = normalized.resolve()
    except OSError as exc:
        raise RunnerError("cannot resolve output path %s: %s" % (path, exc)) from exc
    if resolved != normalized:
        raise RunnerError(
            "output path traverses a symlink (%s -> %s); refusing" % (normalized, resolved)
        )


def _reject_dangerous_explicit(path: Path) -> None:
    """拒绝文件系统根、当前仓库根、裸相对名这类危险输出目标。"""
    normalized = _normalized(path)
    if normalized.parent == normalized:
        raise RunnerError("refusing to use filesystem root as output: %s" % normalized)
    cwd = _normalized(Path.cwd())
    if normalized == cwd:
        raise RunnerError("refusing to use repository root as output: %s" % normalized)
    if Path(str(path)).parent == Path("."):
        raise RunnerError(
            "refusing bare relative output (parent is '.'): %s" % path
        )


def prepare_output_directory(
    config: RunnerConfig,
    output_parent: str = OUTPUT_PARENT,
    now: Optional[datetime] = None,
) -> Path:
    """创建本次运行的输出目录；只新建，绝不递归删除既有内容。

    - 显式 ``output``：必须不存在（否则 :class:`RunnerError`），逐级创建；
    - 默认路径：``.verify/android-smoke`` 不存在则直接创建；已存在时在
      ``.verify`` 下生成带 ``-1``、``-2`` 后缀的不冲突时间戳子目录；
    - 拒绝文件系统根 / 仓库根 / 裸相对名 / symlink 路径。
    """
    if config.output is not None:
        base = Path(config.output)
        _reject_dangerous_explicit(base)
        _reject_symlinks(base)
        if base.exists() or base.is_symlink():
            raise RunnerError("explicit output directory already exists: %s" % base)
        base.mkdir(parents=True)
        return base

    parent = _normalized(Path.cwd() / output_parent)
    default = parent / Path(DEFAULT_OUTPUT).name
    _reject_symlinks(parent)
    if default.exists() or default.is_symlink():
        stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        candidate = parent / stamp
        index = 0
        while candidate.exists() or candidate.is_symlink():
            index += 1
            candidate = parent / ("%s-%d" % (stamp, index))
        default = candidate
    if default.exists() or default.is_symlink():
        raise RunnerError("default output directory already exists: %s" % default)
    default.mkdir(parents=True)
    return default


def write_json_atomic(path: Path, data: Any) -> None:
    """原子写 JSON：先写同目录临时文件再 ``os.replace``，失败不留半写文件。"""
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    directory = path.parent if str(path.parent) else Path(".")
    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def append_text_atomic(path: Path, text: str) -> None:
    """追加文本：单次 ``write`` 追加并 fsync，避免留下截断内容。"""
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def sha256_file(path: Path) -> str:
    """流式计算文件 SHA256（分块读取，不整读大文件）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_log_entry(entry: Any, line_no: int, raw_line: str) -> Dict[str, Any]:
    """校验一行 mock_requests.log 记录；任何问题抛带行号的 :class:`RunnerError`。"""
    where = "mock_requests.log line %d" % line_no
    if not isinstance(entry, dict):
        raise RunnerError(
            "%s: entry must be a JSON object, got %s" % (where, type(entry).__name__)
        )
    missing = [key for key in REQUIRED_LOG_FIELDS if key not in entry]
    if missing:
        raise RunnerError("%s: missing required field(s): %s" % (where, missing))
    extra = [key for key in entry if key not in REQUIRED_LOG_FIELDS]
    if extra:
        raise RunnerError("%s: unexpected field(s): %s" % (where, sorted(extra)))
    for key in ("timestamp_utc", "method", "path"):
        value = entry[key]
        if not isinstance(value, str) or not value:
            raise RunnerError(
                "%s: %s must be a non-empty string, got %r" % (where, key, value)
            )
    if not entry["path"].startswith("/"):
        raise RunnerError(
            "%s: path must start with '/', got %r" % (where, entry["path"])
        )
    query_keys = entry["query_keys"]
    if not isinstance(query_keys, list) or any(
        not isinstance(k, str) for k in query_keys
    ):
        raise RunnerError(
            "%s: query_keys must be a list of strings, got %r" % (where, query_keys)
        )
    body_len = entry["body_len"]
    if not isinstance(body_len, int) or isinstance(body_len, bool) or body_len < 0:
        raise RunnerError(
            "%s: body_len must be a non-negative int, got %r" % (where, body_len)
        )
    return entry


def parse_mock_log(lines: Sequence[str]) -> List[Dict[str, Any]]:
    """解析 mock_requests.log 的 JSONL 行（跳过空行）；坏行抛 :class:`RunnerError`。"""
    entries: List[Dict[str, Any]] = []
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RunnerError(
                "mock_requests.log line %d: invalid JSON: %s" % (line_no, exc)
            ) from exc
        entries.append(_validate_log_entry(entry, line_no, line))
    return entries


def analyze_mock_requests(
    entries: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, int], List[str]]:
    """按契约统计请求并收集违规描述。

    - 预期外的 method/path 计入 unexpected；
    - ``/api/v1/audit`` 的 query_keys 必须精确等于 ``["limit"]``，否则
      同样计入 unexpected；
    - 重复请求按多次计数（expected 与 unexpected 均逐条累计）；
    - 返回 ``(stats, problems)``，满足 expected + unexpected == total。
    """
    stats = {"expected": 0, "unexpected": 0, "total": len(entries)}
    problems: List[str] = []
    for index, entry in enumerate(entries, start=1):
        key = (entry["method"], entry["path"])
        if key not in EXPECTED_ENDPOINTS:
            stats["unexpected"] += 1
            problems.append(
                "entry %d: unexpected method/path: %s %s"
                % (index, entry["method"], entry["path"])
            )
        elif entry["path"] == AUDIT_PATH and entry["query_keys"] != AUDIT_REQUIRED_QUERY_KEYS:
            stats["unexpected"] += 1
            problems.append(
                "entry %d: %s query_keys must be %s, got %s"
                % (index, AUDIT_PATH, AUDIT_REQUIRED_QUERY_KEYS, entry["query_keys"])
            )
        else:
            stats["expected"] += 1
    assert stats["expected"] + stats["unexpected"] == stats["total"]
    return stats, problems


def assert_mock_contract(entries: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """断言全部请求符合契约；有违规时抛 :class:`RunnerError`，否则返回 stats。"""
    stats, problems = analyze_mock_requests(entries)
    if problems:
        raise RunnerError(
            "mock contract violations (%d): %s"
            % (stats["unexpected"], "; ".join(problems))
        )
    return stats


@dataclass
class Stage:
    """单个阶段的执行状态；``status`` 为 running/passed/failed/skipped 之一。"""

    name: str
    status: str
    detail: str = ""
    artifacts: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """输出 build_summary 需要的阶段 dict。"""
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "artifacts": list(self.artifacts),
        }


class StageRecorder:
    """阶段状态纯记录器：不碰文件系统，只登记事实。

    异常路径上调用 :meth:`fail` 即可把当前 running 阶段标记为 failed，
    已通过 :meth:`record_artifact` 登记的产物原样保留。
    """

    def __init__(self) -> None:
        self._stages: List[Stage] = []

    def start(self, name: str) -> Stage:
        stage = Stage(name=name, status="running")
        self._stages.append(stage)
        return stage

    def succeed(self, stage: Stage, detail: str = "") -> Stage:
        stage.status = "passed"
        stage.detail = detail
        return stage

    def skip(self, name: str, detail: str) -> Stage:
        stage = Stage(name=name, status="skipped", detail=detail)
        self._stages.append(stage)
        return stage

    def fail(self, stage: Stage, detail: str) -> Stage:
        stage.status = "failed"
        stage.detail = detail
        return stage

    def record_artifact(self, stage: Stage, path: Path) -> None:
        stage.artifacts.append(Path(path).as_posix())

    def stages(self) -> List[Dict[str, Any]]:
        """build_summary 需要的 stages list（dict 形式）。"""
        return [stage.as_dict() for stage in self._stages]

    def has_failed(self) -> bool:
        return any(stage.status == "failed" for stage in self._stages)


class SmokeRunner:
    """核心编排器：adb/ui/server 全部依赖注入，本类不做真实 IO。

    设备阶段顺序固定：wait_device -> install_apk(可选) -> clear_app ->
    clear_logcat -> start_activity -> 等待首页 -> 依次点击 tab。
    每个阶段调用 :meth:`capture_evidence` 保存 ``NN-stage.xml`` 与
    ``NN-stage.png``；收尾 ``dump_logcat`` 保存 ``logcat-full.txt`` 并生成
    ``summary.json``。
    """

    STAGE_NAMES = {
        "wait_device": "wait-device",
        "install": "install-apk",
        "clear_app": "clear-app",
        "clear_logcat": "clear-logcat",
        "start_activity": "start-activity",
        "home": "wait-home",
        **{key: key for key, _text in TAB_STAGES},
        "search": "search",
        "governance": "governance",
        "contract": "mock-contract",
        "logcat": "dump-logcat",
    }

    SEARCH_EVIDENCE_INDEX = 6
    GOVERNANCE_EVIDENCE_INDEX = 7

    def __init__(
        self,
        config: RunnerConfig,
        adb: Any,
        ui: Any,
        server_factory: Callable[[Path, int], Any],
        output_dir: Path,
        apk_sha256: Optional[str] = None,
        now: Optional[Callable[[], datetime]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.adb = adb
        self.ui = ui
        self.server_factory = server_factory
        self.output_dir = Path(output_dir)
        self.apk_sha256 = apk_sha256
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self.recorder = StageRecorder()
        self.server_stop_error: Optional[str] = None
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None

    # -- 证据采集 -------------------------------------------------------

    def capture_evidence(self, stage_name: str, index: int) -> List[Path]:
        """保存当前 UI 层次 XML 与截图，产物名固定为 NN-stage.xml/png。"""
        prefix = "%02d-%s" % (index, stage_name)
        artifacts = []
        xml_path = self.output_dir / (prefix + ".xml")
        xml_path.write_bytes(self.adb.dump_ui())
        artifacts.append(xml_path)
        png_path = self.output_dir / (prefix + ".png")
        png_path.write_bytes(self.adb.capture_screenshot())
        artifacts.append(png_path)
        return artifacts

    def _wait_for_home(self) -> None:
        self.ui.wait_for(HOME_TEXT, HOME_WAIT_TIMEOUT)

    def _record_stage_artifacts(self, stage: Stage, artifacts: List[Path]) -> None:
        for path in artifacts:
            self._record_artifact(stage, path)

    def _stage(self, key: str) -> Stage:
        return self.recorder.start(self.STAGE_NAMES[key])

    def _record_artifact(self, stage: Stage, path: Path) -> None:
        """登记产物：summary 中只记录相对 output_dir 的文件名，不泄漏绝对路径。"""
        try:
            relative = Path(path).relative_to(self.output_dir)
        except ValueError:
            relative = Path(path)
        self.recorder.record_artifact(stage, relative)

    # -- 主流程 ---------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        """执行整个冒烟流程并写 summary.json；失败时如实重抛原始异常。"""
        requests_log = self.output_dir / MOCK_REQUESTS_LOG
        requests_log.touch(exist_ok=True)
        self.started_at = self.now()

        server = self.server_factory(requests_log, self.config.port)
        server_error: Optional[BaseException] = None
        try:
            server.start()
            self._run_device_stages()
        except BaseException as exc:  # noqa: BLE001 - 需要采集证据后原样重抛
            server_error = exc
            self._record_failure(exc)
        finally:
            self._stop_server(server)

        try:
            summary = self._finalize()
        except BaseException:
            if server_error is not None:
                raise server_error from None
            raise
        if server_error is not None:
            raise server_error
        if self.server_stop_error is not None:
            raise RunnerError("server stop failed: %s" % self.server_stop_error)
        return summary

    def _stop_server(self, server: Any) -> None:
        try:
            server.stop()
        except BaseException as exc:  # noqa: BLE001 - 记录但不吞
            self.server_stop_error = "%s: %s" % (type(exc).__name__, exc)

    def _run_device_stages(self) -> None:
        stage = self._stage("wait_device")
        self.adb.wait_device()
        self.recorder.succeed(stage)

        if not self.config.skip_install:
            stage = self._stage("install")
            self.adb.install_apk(self.config.apk)
            self.recorder.succeed(stage)

        stage = self._stage("clear_app")
        self.adb.clear_app(PACKAGE_NAME)
        self.recorder.succeed(stage)

        stage = self._stage("clear_logcat")
        self.adb.clear_logcat()
        self.recorder.succeed(stage)

        stage = self._stage("start_activity")
        self.adb.start_activity(PACKAGE_NAME, ACTIVITY_NAME)
        self.recorder.succeed(stage)

        stage = self._stage("home")
        self._wait_for_home()
        self.recorder.succeed(stage)

        for index, (stage_key, tab_text) in enumerate(TAB_STAGES, start=1):
            stage = self._stage(stage_key)
            self.ui.tap_text(tab_text)
            self.recorder.succeed(stage)
            artifacts = self.capture_evidence(stage_key, index)
            self._record_stage_artifacts(stage, artifacts)

        self._run_search_stage()
        self._run_governance_stage()

    def _run_search_stage(self) -> None:
        """搜索 UI 流程：搜索源 -> 预览计划 -> 搜索 -> 回查 #9001。

        只通过注入 ``ui`` 的 tap_text / tap_text_occurrence / wait_for /
        type_text / swipe 驱动；输入严格 ASCII（type_text 自身也只接受
        ASCII 可打印字符）。
        """
        stage = self._stage("search")
        self.ui.tap_text(SEARCH_TAB_TEXT)
        self.ui.wait_for(SEARCH_PROVIDERS_TEXT, STAGE_WAIT_TIMEOUT)
        self.ui.tap_text(SEARCH_INPUT_HINT)
        self.ui.type_text(SEARCH_QUERY)
        self.ui.tap_text(PREVIEW_PLAN_TEXT)
        self.ui.wait_for(PLAN_PREVIEW_TEXT, STAGE_WAIT_TIMEOUT)
        # 搜索页 exact "搜索" 至少出现 3 次（页面标题 / 执行按钮 / 底部导航）；
        # 执行按钮是第 2 个（occurrence=1），点击标题或导航会卡住流程。
        self.ui.tap_text_occurrence(SEARCH_RUN_TEXT, occurrence=1)
        self.ui.wait_for(SEARCH_SUMMARY_TEXT, STAGE_WAIT_TIMEOUT)
        self.ui.wait_for(SEARCH_QUERY_ID_TEXT, STAGE_WAIT_TIMEOUT)
        # 回查：先收起软键盘（查询框仍持有焦点），等 IME 收起动画完成再
        # 两次向上滚动到回查区域；两次 swipe 之间等 LazyColumn settle，
        # 否则第二次被滚动动画吞掉。第二次 swipe 之后不额外 sleep：
        # tap_text 的 dump/查找自然等待。
        self.ui.back()
        self.sleep(LOOKUP_KEYBOARD_DISMISS_SECONDS)
        self.ui.swipe(*LOOKUP_SCROLL_ARGS)
        self.sleep(LOOKUP_SCROLL_SETTLE_SECONDS)
        self.ui.swipe(*LOOKUP_SCROLL_ARGS)
        self.ui.tap_text(LOOKUP_INPUT_HINT)
        self.ui.type_text(LOOKUP_QUERY)
        # 输入后软键盘仍打开，「回查记录」按钮坐标被 IME 覆盖，点击会被
        # 输入法转换成字符（r6 证据：输入框出现 "9001g" 且无 lookup GET）；
        # 先 back() 收起键盘，等 IME 收起动画完成再点按钮。
        self.ui.back()
        self.sleep(LOOKUP_KEYBOARD_DISMISS_SECONDS)
        self.ui.tap_text(LOOKUP_RUN_TEXT)
        self.ui.wait_for(LOOKUP_RESULT_TEXT, STAGE_WAIT_TIMEOUT)
        self.recorder.succeed(stage)
        self._record_stage_artifacts(
            stage, self.capture_evidence("search", self.SEARCH_EVIDENCE_INDEX)
        )

    def _run_governance_stage(self) -> None:
        """治理只读流程：回首页 -> 治理 / 发布 -> 区块等待 + 滚动 -> back。

        治理页可滚动：发布版本 / 运行快照首屏可见，审计留痕在下方；
        先等前两个区块出现（确认页面与 API 就绪），向上滚动一次让审计
        留痕进入视口，再等待它出现。治理页不做任何写操作。
        """
        stage = self._stage("governance")
        self.ui.tap_text(GOVERNANCE_HOME_TEXT)
        self.ui.tap_text(GOVERNANCE_ENTRY_TEXT)
        for section in GOVERNANCE_SECTIONS:
            if section == GOVERNANCE_AUDIT_SECTION:
                break
            self.ui.wait_for(section, STAGE_WAIT_TIMEOUT)
        self.ui.swipe(*GOVERNANCE_AUDIT_SCROLL_ARGS)
        self.ui.wait_for(GOVERNANCE_AUDIT_SECTION, STAGE_WAIT_TIMEOUT)
        self.ui.back()
        self.recorder.succeed(stage)
        self._record_stage_artifacts(
            stage,
            self.capture_evidence("governance", self.GOVERNANCE_EVIDENCE_INDEX),
        )

    def _record_failure(self, exc: BaseException) -> None:
        """失败时尽量采集当前 UI/XML/logcat/summary，标记 failed。"""
        for stage in self.recorder._stages:
            if stage.status == "running":
                self.recorder.fail(
                    stage, "%s: %s" % (type(exc).__name__, exc)
                )
        try:
            index = len(self.recorder._stages)
            artifacts = self.capture_evidence("failure", index)
            failed = [
                s for s in self.recorder._stages if s.status == "failed"
            ]
            for path in artifacts:
                for stage in failed:
                    self._record_artifact(stage, path)
        except Exception:
            pass

    def _display_output_dir(self) -> str:
        """summary 中的 output_dir：能相对 cwd 就用相对路径，避免泄漏绝对路径。"""
        try:
            return self.output_dir.relative_to(Path.cwd()).as_posix()
        except ValueError:
            return self.output_dir.name

    def _check_request_contract(self) -> Dict[str, int]:
        """解析 mock_requests.log 并做契约断言；结果写入 mock-contract 阶段。

        解析失败（坏 JSON / 字段问题）抛 :class:`RunnerError` 属于数据错误，
        这里捕获后标记阶段 failed 并在 detail 中保留原始信息，stats 记 0。
        """
        stage = self._stage("contract")
        log_path = self.output_dir / MOCK_REQUESTS_LOG
        try:
            entries = parse_mock_log(
                log_path.read_text(encoding="utf-8").splitlines()
            )
            stats, problems = analyze_mock_requests(entries)
        except RunnerError as exc:
            self.recorder.fail(stage, str(exc))
            return {"expected": 0, "unexpected": 0, "total": 0}
        if problems:
            self.recorder.fail(
                stage,
                "mock contract violations (%d): %s"
                % (stats["unexpected"], "; ".join(problems)),
            )
        else:
            self.recorder.succeed(stage)
        return stats

    def _finalize(self) -> Dict[str, Any]:
        self.finished_at = self.now()
        request_stats = self._check_request_contract()

        logcat_bytes = self.adb.dump_logcat()
        (self.output_dir / LOGCAT_FULL).write_bytes(logcat_bytes)

        logcat_stage = self._stage("logcat")
        logcat_stats = analyze_logcat(
            logcat_bytes.decode("utf-8", errors="replace").splitlines(),
            package_name=PACKAGE_NAME,
        )
        if logcat_stats["has_blocking_issue"]:
            self.recorder.fail(logcat_stage, "logcat blocking issue detected")
        else:
            self.recorder.succeed(logcat_stage)

        summary = build_summary(
            config={
                "serial": self.config.serial,
                "apk_sha256": self.apk_sha256,
                "package_name": PACKAGE_NAME,
                "started_at_utc": self.started_at.isoformat()
                if self.started_at
                else None,
                "finished_at_utc": self.finished_at.isoformat(),
                "output_dir": self._display_output_dir(),
            },
            stages=self.recorder.stages(),
            request_stats=dict(request_stats),
            logcat_stats=logcat_stats,
        )
        write_json_atomic(self.output_dir / SUMMARY_JSON, summary)
        return summary


def _make_server_factory() -> Callable[[Path, int], Any]:
    """真实 CLI 的 server factory；LoopbackMockServer 惰性加载（可被注入替换）。"""

    def factory(log_path: Path, port: int) -> Any:
        server_cls = LoopbackMockServer
        if server_cls is None:
            try:
                from tools.android_smoke.server import LoopbackMockServer as cls
            except ImportError as exc:
                raise RunnerError(
                    "LoopbackMockServer is not available (tools/android_smoke/"
                    "server.py): %s" % exc
                ) from exc
            server_cls = cls
        return server_cls(log_path, port)

    return factory


def _run_once(config: RunnerConfig, output_dir: Path) -> Dict[str, Any]:
    """组装真实依赖并执行一次冒烟；失败时 SmokeRunner 已写 summary 并重抛。"""
    adb = AdbClient(serial=config.serial)
    ui = AndroidUiController(adb=adb)
    apk_sha256 = (
        sha256_file(Path(config.apk)) if config.apk is not None else None
    )
    runner = SmokeRunner(
        config=config,
        adb=adb,
        ui=ui,
        server_factory=_make_server_factory(),
        output_dir=output_dir,
        apk_sha256=apk_sha256,
    )
    return runner.run()


def _read_exit_code(output_dir: Optional[Path]) -> Optional[int]:
    """若失败路径上已有 summary.json，读其 exit_code（读不出返回 None）。"""
    if output_dir is None:
        return None
    summary_path = output_dir / SUMMARY_JSON
    if not summary_path.is_file():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        code = summary["exit_code"]
        return code if isinstance(code, int) else None
    except Exception:  # noqa: BLE001 - 尽力而为，失败回退 1
        return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口：组装真实依赖执行冒烟，按 summary exit_code 退出。

    任何失败都保留已产出的证据目录（绝不清理），优先按 summary 的
    exit_code 退出，其次回退 1。
    """
    output_dir: Optional[Path] = None
    try:
        config = build_config(argv)
        output_dir = prepare_output_directory(config)
        summary = _run_once(config, output_dir)
        print("summary: %s" % (output_dir / SUMMARY_JSON))
        return int(summary["exit_code"])
    except BaseException as exc:  # noqa: BLE001 - 失败也要报告证据位置
        exit_code = _read_exit_code(output_dir)
        if output_dir is not None:
            print(
                "evidence kept in: %s" % output_dir, file=sys.stderr
            )
        print(
            "android-smoke failed: %s: %s" % (type(exc).__name__, exc),
            file=sys.stderr,
        )
        return exit_code if exit_code is not None else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
