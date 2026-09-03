"""M10-06 审计链库外锚定：把 DB head_hash 定期追加到库外 append-only 锚文件。

动机（M10-04 如实声明的边界）：数据库内哈希链可检测行级篡改与漏记，
但持数据库写权限者可整链重算。锚定把每个时刻的 head（sequence + hash）
写到**数据库之外**的 JSONL 锚文件并自身成链（anchor_hash 链接前一锚点），
重算后的 DB 链在既有锚点 sequence 上的 entry_hash 必然对不上，交叉核对
即可发现。

锚文件每行一个 JSON 对象，字段集固定且**不含任何敏感信息**：
schema_version / algorithm / sequence / head_hash / anchored_at /
previous_anchor_hash / anchor_hash。anchor_hash =
sha256(canonical JSON of 其余六字段)——与 DB 链同源的 canonical 规则
（app.domain.audit_chain.canonical_json_bytes），锚文件自身成链。

安全边界（docs/DEVELOPMENT.md 审计节同步维护）：
- 本工具只保证「追加前完整校验 + 单行原子追加 + fsync +（新建时，
  支持目录 fsync 的 POSIX 平台）父目录 fsync」；三步任一失败都进入
  同一失败回截保护区（ftruncate 回原大小 + 尽力 fsync），报失败时
  锚行不在盘上，重跑不会误判 up-to-date；
- 打开/创建语义可区分（不盲开 O_CREAT）：既有文件以 O_WRONLY|O_APPEND
  打开，POSIX 平台附带 O_NOFOLLOW 在 open 处即拒绝 symlink；文件不存在
  才以 O_CREAT|O_EXCL 排他新建（0600），并发窗口内路径被抢先创建/替换
  则 FileExistsError 失败退出，不猜测、不覆盖。Windows 无 O_NOFOLLOW，
  依赖前置 check_anchor_path 的 symlink 拒绝 + 打开后 fstat 常规文件
  复核，两者之间仍存在 symlink swap 残余竞态窗口（docs 如实声明）；
  Windows CRT 的 O_EXCL 会跟随 dangling symlink，故新建后另复核路径
  本身不是链接；
- 锚文件本身是可变文件系统对象，**必须另行复制到 WORM/对象锁/离线介质**
  才构成对持库写权限者的防御——本机锚文件只是操作见证；
- 并发边界：DB 侧在**同一连接同一事务快照**内完成 verify + 锚点交叉
  核对 + head 提取（PG 提升 REPEATABLE READ，SQLite 显式事务快照），
  不引入后台服务与文件锁；两名操作员同时向同一锚文件追加会立刻造成
  previous_anchor_hash 断链，被下一次校验 fail-closed 发现（可检测）。
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db.session import create_engine
from app.domain.audit_chain import (
    GENESIS_PREVIOUS_HASH,
    canonical_datetime,
    canonical_json_bytes,
)
from app.ops.audit_chain_verify import load_chain_snapshot, verify_chain_snapshot

#: 锚文件 schema 版本（锚行字段集/语义变更时递增，校验只认当前值）
ANCHOR_SCHEMA_VERSION = 1

#: 锚链哈希算法（当前与 DB 链同为 sha256；独立常量，允许日后单独演进）
ANCHOR_ALGORITHM = "sha256"

#: 锚行完整字段集（exact match：多字段/少字段都拒绝）
_ANCHOR_FIELDS = frozenset(
    {
        "algorithm",
        "anchor_hash",
        "anchored_at",
        "head_hash",
        "previous_anchor_hash",
        "schema_version",
        "sequence",
    }
)

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

#: 问题输出上限，与 verifier 一致（防海量锚点刷屏）
_MAX_PROBLEMS = 50

#: 追加写入的公共 open 旗标（O_BINARY 仅 Windows 存在）
_APPEND_OPEN_FLAGS = os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0)

#: POSIX O_NOFOLLOW：打开既有文件时在内核处拒绝 symlink（防 swap TOCTOU）。
#: 模块级常量便于测试注入能力分支；Windows 无此旗标（=0，靠前置 symlink
#: 拒绝 + fstat 复核，残余竞态见 docs 声明）。
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

#: POSIX O_DIRECTORY：具备该旗标即视为支持目录 fsync；Windows 无法打开
#: 目录 fd，新建文件后跳过父目录同步（能力边界，docs 如实声明）。
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_CAN_FSYNC_DIR = _O_DIRECTORY != 0


class AnchorInputError(Exception):
    """锚文件路径/参数问题（CLI 退出码 2：不是链结论，是输入不可用）。"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def compute_anchor_hash(anchor: Mapping[str, Any]) -> str:
    """sha256(canonical JSON of 锚行除 anchor_hash 外的全部字段)。"""
    payload = {k: anchor[k] for k in _ANCHOR_FIELDS if k != "anchor_hash"}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def build_anchor(
    *,
    sequence: int,
    head_hash: str,
    previous_anchor_hash: str,
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    """构造一条完整锚行（含 anchor_hash）；字段值经入口校验。"""
    if sequence < 0:
        raise ValueError("sequence 必须 >= 0")
    if not _HEX64_RE.fullmatch(head_hash or ""):
        raise ValueError("head_hash 必须是 64 位小写十六进制")
    if not _HEX64_RE.fullmatch(previous_anchor_hash or ""):
        raise ValueError("previous_anchor_hash 必须是 64 位小写十六进制")
    anchor: dict[str, Any] = {
        "schema_version": ANCHOR_SCHEMA_VERSION,
        "algorithm": ANCHOR_ALGORITHM,
        "sequence": int(sequence),
        "head_hash": head_hash,
        "anchored_at": canonical_datetime(clock()),
        "previous_anchor_hash": previous_anchor_hash,
    }
    anchor["anchor_hash"] = compute_anchor_hash(anchor)
    return anchor


def render_anchor_line(anchor: Mapping[str, Any]) -> bytes:
    """锚行落盘形态：canonical JSON + 单个换行（一行一锚，无空行）。"""
    return canonical_json_bytes(dict(anchor)) + b"\n"


# --- 锚文件路径与解析 -------------------------------------------------------


def check_anchor_path(path: Path) -> None:
    """路径护栏：拒绝 symlink/非常规文件/目录；新建要求父目录已是目录。

    fail-closed：不自动创建目录，不接受非常规文件。AnchorInputError 由
    CLI 映射为退出码 2。
    """
    if os.path.lexists(path):
        if path.is_symlink():
            raise AnchorInputError(f"锚文件是符号链接，拒绝使用: {path}")
        if not path.is_file():
            raise AnchorInputError(
                f"锚文件不是常规文件（目录或设备），拒绝使用: {path}"
            )
        return
    parent = path.parent
    if not parent.is_dir():
        raise AnchorInputError(
            f"锚文件不存在且父目录不是目录（不自动创建）: {parent}"
        )


def load_anchor_file(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """解析并完整校验既有锚文件，返回 (完好锚行, problems)。

    任何格式问题（非 UTF-8 / 空行 / partial line / 非法 JSON / 字段集或
    类型不符 / anchor_hash 重算不匹配 / 锚链链接断裂 / sequence 非严格
    递增 / sequence=0 非 genesis）都进 problems——**不自动修复**，由调用
    方按 invalid 拒绝锚定。文件不存在返回 ([], [])（新建锚文件是合法
    初始状态）；空文件（0 字节）同为合法（尚未锚定）。
    """
    problems: list[str] = []
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return [], ["锚文件不是有效 UTF-8，拒绝锚定（不自动修复）"]
    if text == "":
        return [], []
    if not text.endswith("\n"):
        return [], [
            (
                "锚文件末尾是残缺行（不以换行结束，可能写入中断），"
                "拒绝锚定（不自动修复）"
            )
        ]

    anchors: list[dict[str, Any]] = []
    for index, line in enumerate(text.split("\n")[:-1], start=1):
        if line.strip() == "":
            problems.append(f"第 {index} 行是空行，锚文件非法")
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"第 {index} 行不是合法 JSON，拒绝锚定（不自动修复）")
            continue
        anchor, line_problems = _validated_anchor(obj, index)
        problems.extend(line_problems)
        if anchor is not None:
            anchors.append(anchor)

    _verify_anchor_chain(anchors, problems)
    return anchors, problems


def _validated_anchor(
    obj: Any, index: int
) -> tuple[dict[str, Any] | None, list[str]]:
    """单行结构与字段校验；失败返回 (None, problems) 不参与链校验。"""
    problems: list[str] = []
    if not isinstance(obj, dict):
        return None, [f"第 {index} 行不是 JSON 对象"]
    keys = frozenset(obj)
    if keys != _ANCHOR_FIELDS:
        missing = sorted(_ANCHOR_FIELDS - keys)
        extra = sorted(keys - _ANCHOR_FIELDS)
        detail = []
        if missing:
            detail.append(f"缺字段 {missing}")
        if extra:
            detail.append(f"多字段 {extra}")
        return None, [f"第 {index} 行字段集不符: {'; '.join(detail)}"]

    if obj["schema_version"] != ANCHOR_SCHEMA_VERSION or isinstance(
        obj["schema_version"], bool
    ):
        return None, [
            f"第 {index} 行 schema_version 非 {ANCHOR_SCHEMA_VERSION}"
        ]
    if obj["algorithm"] != ANCHOR_ALGORITHM:
        return None, [
            f"第 {index} 行 algorithm 非 {ANCHOR_ALGORITHM}: {obj['algorithm']}"
        ]
    sequence = obj["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        return None, [f"第 {index} 行 sequence 非法: {sequence!r}"]
    for field in ("head_hash", "previous_anchor_hash", "anchor_hash"):
        value = obj[field]
        if not isinstance(value, str) or not _HEX64_RE.fullmatch(value):
            return None, [f"第 {index} 行 {field} 非 64 位小写十六进制"]
    if not isinstance(obj["anchored_at"], str):
        return None, [f"第 {index} 行 anchored_at 不是字符串"]
    try:
        datetime.fromisoformat(obj["anchored_at"])
    except ValueError:
        return None, [f"第 {index} 行 anchored_at 不是合法 ISO 8601"]

    anchor = dict(obj)
    if compute_anchor_hash(anchor) != anchor["anchor_hash"]:
        return None, [
            f"第 {index} 行 anchor_hash 与字段重算不匹配（锚行被篡改）"
        ]
    return anchor, problems


def _verify_anchor_chain(
    anchors: list[dict[str, Any]], problems: list[str]
) -> None:
    """锚链自身成链：previous_anchor_hash 链接 + sequence 严格递增。"""
    previous_hash = GENESIS_PREVIOUS_HASH
    previous_sequence: int | None = None
    for index, anchor in enumerate(anchors, start=1):
        if anchor["previous_anchor_hash"] != previous_hash:
            problems.append(
                f"第 {index} 行 previous_anchor_hash 与前一锚点 anchor_hash "
                "不链接（锚链断裂或被插入/重排）"
            )
        if previous_sequence is not None and anchor["sequence"] <= previous_sequence:
            problems.append(
                f"第 {index} 行 sequence={anchor['sequence']} 未严格递增"
                f"（前一锚点 {previous_sequence}；重复或回退锚点）"
            )
        if anchor["sequence"] == 0 and anchor["head_hash"] != GENESIS_PREVIOUS_HASH:
            problems.append(
                f"第 {index} 行 sequence=0 的 head_hash 非 genesis 常量"
            )
        previous_hash = anchor["anchor_hash"]
        previous_sequence = anchor["sequence"]


# --- DB 快照（与 verifier 同一连接、同一事务） ------------------------------


async def load_verified_snapshot(db_url: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """单连接单事务快照读取并校验 DB 链，返回 (snapshot, verify_report)。

    verify 结论、锚点交叉核对、head 提取全部基于同一份快照——不存在
    「先 verifier 一条连接、后锚定又一条连接」之间的竞态。PG 连接提升
    REPEATABLE READ（三条 SELECT 同一快照）；SQLite 走显式事务的库级
    快照。对数据库零写入。
    """
    engine = create_engine(db_url)
    try:
        async with engine.connect() as conn:
            if conn.dialect.name == "postgresql":
                conn.execution_options(isolation_level="REPEATABLE READ")
            async with conn.begin():
                snapshot = await load_chain_snapshot(conn)
    finally:
        await engine.dispose()
    return snapshot, verify_chain_snapshot(snapshot)


def cross_check_anchors(
    anchors: list[dict[str, Any]],
    snapshot: Mapping[str, Any],
    problems: list[str],
) -> None:
    """每个历史锚点 (sequence, head_hash) 与当前 DB 对应 entry 交叉核对。

    sequence=0 对 genesis 常量；sequence>=1 必须命中当前 DB 链同一
    sequence 的 entry 且 entry_hash 一致。DB 整链重算（同 sequence 不同
    hash）、DB 回退（锚点 sequence 在当前链不存在）都在此暴露。
    """
    entry_hash_by_sequence = {
        entry["sequence"]: entry["entry_hash"] for entry in snapshot["entries"]
    }
    for anchor in anchors:
        sequence = anchor["sequence"]
        if sequence == 0:
            continue  # genesis 常量校验在锚链解析完成
        actual = entry_hash_by_sequence.get(sequence)
        if actual is None:
            problems.append(
                f"锚点 sequence={sequence} 在当前 DB 链中不存在"
                "（DB 回退或重建），拒绝锚定"
            )
        elif actual != anchor["head_hash"]:
            problems.append(
                f"锚点 sequence={sequence} head_hash 与当前 DB entry_hash "
                "不匹配（DB 整链重算或该位置被重写），拒绝锚定"
            )


# --- 追加写入（append-only + fsync + 半行回滚） -----------------------------


def _open_existing(path: Path) -> int:
    """打开既有锚文件：O_WRONLY|O_APPEND，不带 O_CREAT。

    POSIX 平台附带 O_NOFOLLOW——路径是 symlink 时内核直接 ELOOP（消除
    前置检查与打开之间的 swap TOCTOU 窗口），转成 AnchorInputError。
    其余 OSError（权限、路径不存在等）原样上抛，由调用方分派。
    """
    try:
        return os.open(path, _APPEND_OPEN_FLAGS | _O_NOFOLLOW)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise AnchorInputError(f"锚文件是符号链接，拒绝写入: {path}") from exc
        raise


def _create_new(path: Path) -> int:
    """排他新建锚文件：O_CREAT|O_EXCL（0600）。

    并发窗口内路径已被他人创建/替换则 FileExistsError 上抛——不猜测、
    不覆盖。Windows CRT 的 O_EXCL 会跟随 dangling symlink 在目标处创建
    （POSIX O_EXCL 检查链接本身），故新建成功后复核路径本身不是符号
    链接；复核命中时只拒绝不清理目标（与 M10-04 导出档案同模式）。
    """
    fd = os.open(path, _APPEND_OPEN_FLAGS | os.O_CREAT | os.O_EXCL, 0o600)
    if not os.path.islink(path):
        return fd
    os.close(fd)
    raise AnchorInputError(f"锚文件路径是符号链接，拒绝创建: {path}")


def _fsync_parent_directory(path: Path) -> None:
    """fsync 父目录，让新建文件的目录项在 crash 后尽量持久。

    能力边界（docs/DEVELOPMENT.md 同步声明，不虚报）：Windows 无法打开
    目录 fd（无 O_DIRECTORY），直接跳过；个别文件系统对目录 fsync 返回
    EINVAL（不支持），按能力跳过；其余 OSError 原样上抛——锚行虽已
    fsync，但持久性承诺未达成时不静默装作成功。
    """
    if not _CAN_FSYNC_DIR:
        return
    dir_fd = os.open(path.parent, os.O_RDONLY | _O_DIRECTORY)
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        if exc.errno != errno.EINVAL:
            raise  # 真实 IO 失败上抛；EINVAL=该文件系统不支持目录 fsync
    finally:
        os.close(dir_fd)


def append_anchor_line(path: Path, line: bytes) -> None:
    """单行原子追加：O_APPEND 单次序列写满 + fsync；失败回截不留半行。

    打开/创建可区分（不盲开 O_CREAT）：先只开既有文件，不存在才排他
    新建（0600，POSIX 语义；Windows 无 POSIX 权限位=默认 ACL，见 docs）。
    POSIX 打开带 O_NOFOLLOW 在内核处拒绝 symlink；Windows 无该旗标，
    靠前置 symlink 拒绝 + 打开后 fstat 常规文件复核，其间仍有残余
    swap 竞态（docs 如实声明）。

    失败回截保护区：写入、文件 fsync、新建后的父目录 fsync 三步任一
    抛 OSError，都尽力 ftruncate 回 original_size 并尽力 fsync 持久化
    回截——报失败时锚行必须不在盘上，否则操作员重跑会看到 up-to-date，
    状态二义。父目录 fsync 失败同样回截（新建文件回到 0 字节空文件=
    合法初始状态；不 unlink：删除目录项又需要目录 fsync，而它正在失败）。
    回截或回截后的 fsync 也失败时仍上抛**原始** OSError，不虚构成功——
    该残余灾难路径（截断未持久化/crash 后状态未知）由下一次完整校验按
    partial line/锚链问题 fail-closed 或人工排查处理。
    """
    try:
        fd = _open_existing(path)
        created = False
    except FileNotFoundError:
        fd = _create_new(path)
        created = True
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise AnchorInputError(f"锚文件打开后不是常规文件，拒绝写入: {path}")
        original_size = os.lseek(fd, 0, os.SEEK_END)
        try:
            view = memoryview(line)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
            if created:
                _fsync_parent_directory(path)
        except OSError:
            try:
                os.ftruncate(fd, original_size)  # 去掉已写入的锚行
                os.fsync(fd)  # 尽力持久化回截（次生失败不掩盖原始错误）
            except OSError:
                pass
            raise
    finally:
        os.close(fd)


# --- 主流程 -----------------------------------------------------------------


async def run_anchor(
    db_url: str | None,
    anchor_file: str | Path,
    *,
    execute: bool = False,
    verify_only: bool = False,
    clock: Callable[[], datetime] | None = None,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """锚定主流程（默认 dry-run；execute=True 且 head 前进才落盘）。

    返回 report dict（status: anchored / up-to-date / dry-run / valid /
    invalid）。步骤（顺序即防线）：
    1. 路径护栏（symlink/非常规文件/父目录缺失 -> AnchorInputError）；
    2. DB 链快照 verify，invalid 即拒绝。默认自建单连接事务快照
       （load_verified_snapshot）；调用方也可传入已加载的 snapshot——
       用于只读消费方（如 production-preflight）让锚定交叉核对与自身
       主检查共享同一快照，不再开第二个连接。verify 结论**始终从
       snapshot 纯重算**（verify_chain_snapshot，无 IO），不接收外部
       verify_report——杜绝「报告与快照不一致」的数据来源歧义。外部
       快照只服务只读路径：与 execute 互斥（真正落盘必须基于现场单
       连接快照）、与 db_url 互斥（两者同时提供时数据来源二义，
       fail-closed 拒绝），db_url 为 None 且未提供快照时
       AnchorInputError；
    3. 完整解析校验既有锚文件，任何 problem 即拒绝（不自动修复）；
    4. 全部历史锚点与 DB 交叉核对（整链重算/回退拒绝）；
    5. DB head == 最后锚点 -> up-to-date 不重复追加；
    6. head 前进：verify_only 报 valid；dry-run 报计划；execute 追加
       单行 + fsync。
    """
    if execute and verify_only:
        raise AnchorInputError("--verify-only 与 --yes 互斥")
    if snapshot is not None and execute:
        raise AnchorInputError(
            "外部快照仅用于只读校验，与 --yes 互斥（执行锚定必须现场单连接快照）"
        )
    if snapshot is not None and db_url is not None:
        raise AnchorInputError(
            "外部快照与 --db-url 互斥：两者同时提供时数据来源二义，必须二选一"
        )
    if snapshot is None and db_url is None:
        raise AnchorInputError("缺少 --db-url：未提供外部快照时必须指定数据库")
    path = Path(anchor_file)
    check_anchor_path(path)
    clock = clock or _utc_now

    problems: list[str] = []
    report: dict[str, Any] = {
        "status": "invalid",
        "anchor_file": {"path": str(path), "anchors": 0},
        "db": {},
        "proposed_anchor": None,
        "written": False,
        "problems": problems,
    }

    if snapshot is None:
        snapshot, verify_report = await load_verified_snapshot(db_url)
    else:
        # 始终从 snapshot 纯重算（无 IO）：不接收外部 verify_report，
        # 结论与快照必然同源，不存在「提供的报告对应另一份快照」的歧义。
        verify_report = verify_chain_snapshot(snapshot)
    report["db"] = {
        "valid": verify_report["valid"],
        "entries": verify_report["entries"],
        "audit_rows": verify_report["audit_rows"],
        "head_sequence": (
            snapshot["states"][0]["last_sequence"]
            if snapshot["states"]
            else None
        ),
        "head_hash": (
            snapshot["states"][0]["last_hash"] if snapshot["states"] else None
        ),
        "algorithm": verify_report["algorithm"],
    }
    problems.extend(verify_report["problems"])
    if not verify_report["valid"]:
        return _finish_report(report)

    if os.path.lexists(path):
        anchors, anchor_problems = load_anchor_file(path)
        problems.extend(anchor_problems)
    else:
        anchors = []
    report["anchor_file"]["anchors"] = len(anchors)
    if anchors:
        report["anchor_file"]["last_sequence"] = anchors[-1]["sequence"]
        report["anchor_file"]["last_head_hash"] = anchors[-1]["head_hash"]
        report["anchor_file"]["last_anchor_hash"] = anchors[-1]["anchor_hash"]
    if problems:
        return _finish_report(report)

    cross_check_anchors(anchors, snapshot, problems)
    head_sequence = report["db"]["head_sequence"]
    assert head_sequence is not None  # verify valid 蕴含 state 单行存在
    if anchors and head_sequence < anchors[-1]["sequence"]:
        problems.append(
            f"DB head sequence={head_sequence} 小于最后锚点 "
            f"sequence={anchors[-1]['sequence']}（DB 回退），拒绝锚定"
        )
    if problems:
        return _finish_report(report)

    head_hash = report["db"]["head_hash"]
    assert isinstance(head_hash, str)
    if anchors and (head_sequence, head_hash) == (
        anchors[-1]["sequence"],
        anchors[-1]["head_hash"],
    ):
        report["status"] = "up-to-date"
        return _finish_report(report)

    previous_anchor_hash = (
        anchors[-1]["anchor_hash"] if anchors else GENESIS_PREVIOUS_HASH
    )
    anchor = build_anchor(
        sequence=head_sequence,
        head_hash=head_hash,
        previous_anchor_hash=previous_anchor_hash,
        clock=clock,
    )
    report["proposed_anchor"] = anchor
    if verify_only:
        report["status"] = "valid"
        return _finish_report(report)
    if not execute:
        report["status"] = "dry-run"
        return _finish_report(report)
    append_anchor_line(path, render_anchor_line(anchor))
    report["written"] = True
    report["anchor_file"]["anchors"] = len(anchors) + 1
    report["status"] = "anchored"
    return _finish_report(report)


def _finish_report(report: dict[str, Any]) -> dict[str, Any]:
    if report["status"] == "invalid":
        report["valid"] = False
    else:
        report["valid"] = True
    return report


def format_anchor_summary(report: Mapping[str, Any]) -> str:
    """人类可读摘要（只含 sequence/hash/计数，不含任何敏感值）。"""
    status = report["status"]
    db = report["db"]
    anchors = report["anchor_file"]["anchors"]
    if status == "invalid":
        lines = [
            f"audit chain anchor: INVALID（{len(report['problems'])} 个问题）",
            f"db entries={db.get('entries')} anchors={anchors}",
        ]
        lines.extend(f"- {p}" for p in report["problems"][:_MAX_PROBLEMS])
        return "\n".join(lines)
    if status == "anchored":
        anchor = report["proposed_anchor"]
        assert anchor is not None
        return (
            f"audit chain anchor: ANCHORED（已追加锚点 sequence={anchor['sequence']} "
            f"anchor_hash={anchor['anchor_hash'][:12]}… 到 {report['anchor_file']['path']}；"
            f"共 {anchors} 锚点）。锚文件必须另行复制到 WORM/对象锁/离线介质。"
        )
    if status == "up-to-date":
        return (
            f"audit chain anchor: UP-TO-DATE（db head sequence={db['head_sequence']} "
            "与最后锚点一致，未追加）"
        )
    if status == "dry-run":
        anchor = report["proposed_anchor"]
        assert anchor is not None
        return (
            f"audit chain anchor: DRY-RUN（将追加锚点 sequence={anchor['sequence']} "
            f"head_hash={anchor['head_hash'][:12]}…；未写入 {report['anchor_file']['path']}；"
            "确认后加 --yes 执行）"
        )
    # verify-only: valid
    pending = ""
    if report.get("proposed_anchor"):
        pending = (
            f"；db head sequence={report['proposed_anchor']['sequence']} 超前最后锚点，"
            "可执行锚定"
        )
    return (
        f"audit chain anchor: VALID（db {db.get('entries')} entries valid；"
        f"锚文件 {anchors} 锚点自洽且与 DB 交叉一致{pending}）"
    )
