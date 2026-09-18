r"""M14-49 契约测试：审计锚点 WORM 离线第二副本工具（零网络/零 env/零子进程）。

锁定 tools/ops/audit_worm_offline_copy.py 的全部契约面：

- 常量与源级纪律（确认短语/marker 契约/工件目录/零网络零 env 零子进程
  零 DB 零凭据 token）；
- 锚链校验与 M14-42/M14-43 同契约（畸形/链断/symlink/缺文件——独立
  构造锚点，不 import 被测实现）；
- M14-43 verify 成功报告绑定链（形态/status/worm_verified/source 块/
  key/version/COMPLIANCE/retain_until/content-type/对象 hash-size；
  畸形/篡改/跨参数逐项拒绝）；
- 离线根目录与 marker 契约（显式绝对/已存在/常规目录/symlink 含祖先/
  拒绝 repo 内（涵盖 .verify/artifacts）/marker 预创建且字节精确）；
- copy 语义（确认短语门最前置零写入；happy 三文件逐字节；确定性
  manifest；幂等零覆盖；残留/字节不符 fail-closed；写后重读漂移
  fail-closed；布局目录 symlink/非常规拒绝）；
- verify 只读重算（不修复不覆盖；篡改/缺失/跨参数报告换名检测）；
- preflight 既有副本观察（absent/matching 通过；partial/mismatch 拒绝）；
- 报告工件（三命令 JSON+MD+sidecar；随机后缀名注入；碰撞 -2 递增；
  原子写；redact 终防线；意外异常折叠 exit 2 无 traceback 无秘密）；
- RealFS 真实盘端到端（字节模式写盘防 Windows CRLF 翻译；copy→verify
  回环）。

测试全程 FakeFS/FakeClock/FakeSuffixGen 注入——零网络零真实副作用。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "audit_worm_offline_copy.py"


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


awoc = _load_module(SCRIPT, "audit_worm_offline_copy_under_test")

# ---------------------------------------------------------------- 测试常量

STAMP = "20260918-060000"
TOKEN = "7c1d94e0a5f83b71d2e6c40f95a8b3d7"
NOW = datetime(2026, 9, 18, 6, 0, 0, tzinfo=UTC)

ANCHOR_PATH = Path("/opt/audit/audit-anchor.jsonl")
REPORT_PATH = Path("/opt/audit/verify-20260917.json")
REPORT2_PATH = Path("/opt/audit/verify-20260918.json")

# 离线根：绝对路径（Windows 盘符 / POSIX 根皆成立），且必然在仓库外
OFFLINE_ROOT = Path(os.path.abspath(os.path.join(os.sep, "aios-offline-copy-test", "media")))
OFFLINE_PARENT = OFFLINE_ROOT.parent
MARKER_PATH = OFFLINE_ROOT / awoc.MARKER_NAME

# ---------------------------------------------------------------- 锚点构造（独立实现）


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _payload_hash(anchor: dict) -> str:
    payload = {k: v for k, v in anchor.items() if k != "anchor_hash"}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def make_anchor(sequence, head_hash, previous,
                anchored_at="2026-09-17T00:00:00.000000+00:00") -> dict:
    anchor = {"schema_version": 1, "algorithm": "sha256", "sequence": sequence,
              "head_hash": head_hash, "anchored_at": anchored_at,
              "previous_anchor_hash": previous}
    anchor["anchor_hash"] = _payload_hash(anchor)
    return anchor


def rehashed(anchor: dict) -> dict:
    """行级合法但链语义可断（重算 anchor_hash 后序列化）。"""
    out = dict(anchor)
    out["anchor_hash"] = _payload_hash(anchor)
    return out


def jsonl(*anchors) -> bytes:
    return b"".join(_canonical(a) + b"\n" for a in anchors)


def anchor_fact(anchor: dict) -> dict:
    return {"sequence": anchor["sequence"], "head_hash": anchor["head_hash"],
            "anchor_hash": anchor["anchor_hash"]}


GENESIS = "0" * 64
FIRST = make_anchor(0, GENESIS, GENESIS)
SECOND = make_anchor(1, "a" * 64, FIRST["anchor_hash"],
                     anchored_at="2026-09-17T00:01:00.000000+00:00")
THIRD = make_anchor(5, "b" * 64, SECOND["anchor_hash"],
                    anchored_at="2026-09-17T00:02:00.000000+00:00")
TWO_ANCHOR_BYTES = jsonl(FIRST, SECOND)
TWO_ANCHOR_SHA = hashlib.sha256(TWO_ANCHOR_BYTES).hexdigest()
ONE_ANCHOR_BYTES = jsonl(FIRST)
ONE_ANCHOR_SHA = hashlib.sha256(ONE_ANCHOR_BYTES).hexdigest()

# ---------------------------------------------------------------- verify 报告构造（独立实现）


def make_report_payload(anchor_sha=TWO_ANCHOR_SHA,
                        anchor_size=None,
                        anchor_count=2, first=None, last=None) -> dict:
    """M14-43 audit_anchor_archive.py verify 成功报告的真实形态。"""
    if anchor_size is None:
        anchor_size = len(TWO_ANCHOR_BYTES)
    return {
        "schema_version": 1, "milestone": "M14-43", "command": "verify",
        "tool": "audit_anchor_archive",
        "started_at_utc": "2026-09-17T19:20:00Z",
        "ended_at_utc": "2026-09-17T19:20:02Z",
        "status": "pass", "problems": [], "worm_verified": True,
        "source": {"sha256": anchor_sha, "size_bytes": anchor_size,
                   "anchor_count": anchor_count,
                   "first": first or anchor_fact(FIRST),
                   "last": last or anchor_fact(SECOND)},
        "target": {"endpoint_host": "127.0.0.1", "bucket": "audit-archive",
                   "key": f"audit-anchor/{anchor_sha}/audit-anchor.jsonl"},
        "archive": {"created": True, "existing_matched": False,
                    "version_id": "ver-0001",
                    "retention_mode": "COMPLIANCE",
                    "retain_until": "2036-09-17T19:10:00Z",
                    "object_sha256": anchor_sha,
                    "object_size_bytes": anchor_size,
                    "content_type": "application/x-ndjson"},
        "archive_report": {"sha256": "ab" * 32,
                           "name": "archive-20260917-191000-legacy.json"},
    }


def serialize_report(payload: dict) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True,
                      ensure_ascii=False).encode("utf-8") + b"\n"


VERIFY_REPORT_BYTES = serialize_report(make_report_payload())
VERIFY_REPORT_SHA = hashlib.sha256(VERIFY_REPORT_BYTES).hexdigest()


def mutated_report(mutate) -> bytes:
    payload = json.loads(VERIFY_REPORT_BYTES.decode("utf-8"))
    mutate(payload)
    return serialize_report(payload)


def expected_manifest_bytes(anchor_bytes=TWO_ANCHOR_BYTES,
                            report_bytes=VERIFY_REPORT_BYTES,
                            source_name=REPORT_PATH.name,
                            version_id="ver-0001",
                            retain_until="2036-09-17T19:10:00Z") -> bytes:
    """确定性 manifest 的独立复算（锁定纯输入函数语义）。"""
    anchor_sha = hashlib.sha256(anchor_bytes).hexdigest()
    payload = {
        "schema_version": 1, "milestone": "M14-49",
        "tool": "audit_worm_offline_copy",
        "layout": f"audit-anchor/{anchor_sha}",
        "anchor": {"name": "audit-anchor.jsonl", "sha256": anchor_sha,
                   "size_bytes": len(anchor_bytes)},
        "verify_report": {
            "name": "verify-report.json", "source_name": source_name,
            "sha256": hashlib.sha256(report_bytes).hexdigest(),
            "size_bytes": len(report_bytes)},
        "worm": {
            "endpoint_host": "127.0.0.1", "bucket": "audit-archive",
            "key": f"audit-anchor/{anchor_sha}/audit-anchor.jsonl",
            "version_id": version_id, "retention_mode": "COMPLIANCE",
            "retain_until": retain_until,
            "content_type": "application/x-ndjson",
            "object_sha256": anchor_sha,
            "object_size_bytes": len(anchor_bytes)},
    }
    return json.dumps(payload, indent=2, sort_keys=True,
                      ensure_ascii=False).encode("utf-8") + b"\n"


# ---------------------------------------------------------------- 注入替身


class FakeClock:
    def now(self) -> datetime:
        return NOW

    def utc_now_iso(self) -> str:
        return "2026-09-18T06:00:00Z"

    def stamp(self) -> str:
        return STAMP


class FakeSuffixGen:
    """确定性随机后缀（碰撞测试可重复返回同一 token）。"""

    def __init__(self, tokens=None):
        self.tokens = list(tokens or [TOKEN])
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return self.tokens[min(self.calls - 1, len(self.tokens) - 1)]


class FakeFS:
    """内存 FS：files(bytes)/dirs/symlinks/not_regular 四象限建模。"""

    def __init__(self, files=None, dirs=(), symlinks=(), not_regular=()):
        self.files: dict[Path, bytes] = {Path(p): bytes(b) for p, b in (files or {}).items()}
        self.dirs: set[Path] = {Path(p) for p in dirs}
        self.symlinks: set[Path] = {Path(p) for p in symlinks}
        self.not_regular: set[Path] = {Path(p) for p in not_regular}
        self.writes: list[Path] = []
        self.created_dirs: list[Path] = []

    def exists(self, path) -> bool:
        p = Path(path)
        return p in self.files or p in self.dirs or p in self.not_regular

    def is_file(self, path) -> bool:
        p = Path(path)
        return p in self.files and p not in self.not_regular and p not in self.dirs

    def is_dir(self, path) -> bool:
        return Path(path) in self.dirs

    def is_symlink(self, path) -> bool:
        return Path(path) in self.symlinks

    def read_bytes(self, path) -> bytes:
        return self.files[Path(path)]

    def write_bytes_atomic(self, path, data: bytes) -> None:
        self.files[Path(path)] = bytes(data)
        self.writes.append(Path(path))

    def write_text_atomic(self, path, text: str) -> None:
        self.write_bytes_atomic(path, text.encode("utf-8"))

    def mkdirs(self, path) -> None:
        p = Path(path)
        self.dirs.add(p)
        self.created_dirs.append(p)


class ExplodingWriteFS(FakeFS):
    """任何写入都炸（消息带秘密形态值）——锁定终防线零外泄。"""

    def write_bytes_atomic(self, path, data: bytes) -> None:
        raise OSError("password=hunter2 api_key=abc123 bearer xyz")


class ReadBackCorruptFS(FakeFS):
    """指定文件写后被读时返回漂移字节（模拟落盘后漂移）。"""

    def __init__(self, *args, corrupt=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.corrupt = Path(corrupt)

    def read_bytes(self, path) -> bytes:
        data = super().read_bytes(path)
        if Path(path) == self.corrupt and Path(path) in self.writes:
            return data + b"x"
        return data


def make_fs(*, anchor_bytes=TWO_ANCHOR_BYTES, report_bytes=VERIFY_REPORT_BYTES,
            root=OFFLINE_ROOT, marker=True, marker_content=None,
            offline_files=None, symlinks=(), not_regular=(),
            dirs=()) -> FakeFS:
    files: dict[Path, bytes] = {}
    if anchor_bytes is not None:
        files[ANCHOR_PATH] = anchor_bytes
    if report_bytes is not None:
        files[REPORT_PATH] = report_bytes
    if marker:
        files[Path(root) / awoc.MARKER_NAME] = (
            marker_content if marker_content is not None else awoc.MARKER_CONTENT)
    if offline_files:
        files.update({Path(p): bytes(b) for p, b in offline_files.items()})
    return FakeFS(files=files, dirs={Path(root), *dirs}, symlinks=symlinks,
                  not_regular=not_regular)


# ---------------------------------------------------------------- 运行/断言辅助


def base_args(command, *, anchor=ANCHOR_PATH, report=REPORT_PATH,
              root=OFFLINE_ROOT) -> list[str]:
    return [command, "--anchor-file", str(anchor), "--verify-report",
            str(report), "--offline-root", str(root)]


def preflight_args(**kw) -> list[str]:
    return base_args("preflight", **kw)


def copy_args(confirm=None, **kw) -> list[str]:
    args = base_args("copy", **kw)
    args += ["--confirm",
             confirm if confirm is not None else awoc.CONFIRM_COPY]
    return args


def verify_args(**kw) -> list[str]:
    return base_args("verify", **kw)


def run(args, fs, *, suffix_gen=None) -> int:
    return awoc.main(args, fs=fs, clock=FakeClock(),
                     suffix_gen=suffix_gen or FakeSuffixGen())


def artifact_path(mode: str, ext: str = ".json", suffix: str = "") -> Path:
    return awoc.ARTIFACT_DIR / f"{mode}-{STAMP}-{TOKEN}{suffix}{ext}"


def report_payload(fs, mode: str, suffix: str = "") -> dict:
    return json.loads(fs.read_bytes(artifact_path(mode, ".json", suffix))
                      .decode("utf-8"))


def codes(payload: dict) -> list[str]:
    return [p["code"] for p in payload["problems"]]


def copy_paths(sha=TWO_ANCHOR_SHA, root=OFFLINE_ROOT) -> dict[str, Path]:
    dir_path = Path(root) / "audit-anchor" / sha
    return {"dir": dir_path,
            "anchor": dir_path / "audit-anchor.jsonl",
            "report": dir_path / "verify-report.json",
            "manifest": dir_path / "manifest.json"}


def offline_writes(fs, root=OFFLINE_ROOT) -> list[Path]:
    root = Path(root)
    return [p for p in fs.writes if p == root or root in p.parents]


def artifact_writes(fs) -> list[Path]:
    base = awoc.ARTIFACT_DIR
    return [p for p in fs.writes if p == base or base in p.parents]


def assert_zero_offline_writes(fs) -> None:
    assert offline_writes(fs) == []


# ---------------------------------------------------------------- 常量与源级纪律


class TestConstants:
    def test_confirm_phrase_locked(self):
        assert awoc.CONFIRM_COPY == "EXECUTE AUDIT ANCHOR WORM OFFLINE COPY"
        # 与 M14-43 archive 短语刻意不同（防串用）
        assert awoc.CONFIRM_COPY != "EXECUTE AUDIT ANCHOR WORM ARCHIVE"

    def test_artifact_dir_locked(self):
        expected = (Path(awoc.REPO_ROOT) / ".verify" / "artifacts"
                    / "m14-49-audit-worm-offline-copy")
        assert awoc.ARTIFACT_DIR == expected

    def test_marker_contract_locked(self):
        assert awoc.MARKER_NAME == "AIOS-OFFLINE-COPY-ROOT.marker"
        assert awoc.MARKER_CONTENT == b"AIOS audit anchor WORM offline copy root v1\n"
        # marker 契约是字节精确常量（README 同口径；43 字符正文 + LF = 44）
        assert len(awoc.MARKER_CONTENT) == 44

    def test_layout_constants_locked(self):
        sha = "a" * 64
        assert awoc.worm_object_key(sha) == f"audit-anchor/{sha}/audit-anchor.jsonl"
        assert awoc.layout_dir(Path("/m"), sha) == Path("/m/audit-anchor") / sha
        assert awoc.COPY_NAMES == ("audit-anchor.jsonl", "verify-report.json",
                                   "manifest.json")
        assert awoc.WORM_RETENTION_MODE == "COMPLIANCE"
        assert awoc.WORM_CONTENT_TYPE == "application/x-ndjson"
        assert (awoc.EXIT_OK, awoc.EXIT_REJECT) == (0, 2)


class TestSourceContract:
    def test_source_no_network_subprocess_env(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for token in ("import socket", "urlopen", "urllib", "http.client",
                      "requests", "boto3", "subprocess", "os.environ",
                      "getenv", "shlex", "shell=True"):
            assert token not in source, f"源码不得出现 {token}"

    def test_source_no_db_or_credential_surface(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for token in ("psycopg", "sqlite3", "mongodb", "--access-key",
                      "--secret-key", "--password", "--token",
                      "MINIO_ROOT", "aws_secret", "api_key="):
            assert token not in source, f"源码不得出现 {token}"

    def test_source_positive_discipline(self):
        source = SCRIPT.read_text(encoding="utf-8")
        # CSPRNG 随机后缀 + 原子写 + 字节模式写盘 + 终防线折叠
        assert "secrets.token_hex" in source
        assert "os.replace" in source
        assert "write_bytes" in source
        assert "type(exc).__name__" in source

    def test_redact_secrets_shapes(self):
        assert awoc.redact_secrets("password=hunter2") == "<redacted>"
        assert awoc.redact_secrets("api_key: abc") == "<redacted>"
        assert awoc.redact_secrets("Bearer abc.def.ghi") == "<redacted>"
        # retain_until 是 WORM 保留时间戳（审计事实，Markdown 报告原样
        # 渲染），不是秘密——终防线只折叠秘密 key 名单，不得扩大
        assert awoc.redact_secrets("retain_until=2036-09-17T19:10:00Z") \
            == "retain_until=2036-09-17T19:10:00Z"
        assert awoc.redact_secrets("sha256 d2bf…73aa / 354 bytes") \
            == "sha256 d2bf…73aa / 354 bytes"


# ---------------------------------------------------------------- 锚链校验（与 M14-42/M14-43 同契约）


MALFORMED_ANCHORS = [
    (b"not json\n", "anchor-line-not-json"),
    (b"[1, 2]\n", "anchor-line-not-object"),
    (jsonl({k: v for k, v in FIRST.items() if k != "algorithm"}),
     "anchor-field-set"),
    (jsonl({**FIRST, "extra": 1}), "anchor-field-set"),
    (jsonl({**FIRST, "schema_version": 2}), "anchor-schema-version"),
    (jsonl({**FIRST, "algorithm": "sha512"}), "anchor-algorithm"),
    (jsonl({**FIRST, "sequence": "1"}), "anchor-sequence-type"),
    (jsonl({**FIRST, "head_hash": 123}), "anchor-field-type"),
    (jsonl({**FIRST, "anchored_at": "not-a-date"}),
     "anchor-anchored-at-not-iso8601"),
    (jsonl({**FIRST, "head_hash": "A" * 64}), "anchor-hash-format"),
    (jsonl({**FIRST, "anchor_hash": "c" * 64}), "anchor-hash-mismatch"),
    (json.dumps(FIRST, sort_keys=True,
                separators=(", ", ": ")).encode("utf-8") + b"\n",
     "anchor-not-canonical"),
    (b"", "anchor-file-empty"),
    (TWO_ANCHOR_BYTES[:-1], "anchor-file-no-trailing-newline"),
    (_canonical(FIRST) + b"\n\n" + _canonical(SECOND) + b"\n",
     "anchor-line-empty"),
]


class TestAnchorValidationParity:
    @pytest.mark.parametrize("payload,expected", MALFORMED_ANCHORS)
    def test_malformed_anchor_rejected(self, payload, expected):
        fs = make_fs(anchor_bytes=payload)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == [expected]
        assert_zero_offline_writes(fs)

    def test_anchor_file_missing(self):
        fs = make_fs(anchor_bytes=None)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["anchor-file-missing"]

    def test_anchor_file_symlink(self):
        fs = make_fs(symlinks={ANCHOR_PATH})
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["anchor-file-symlink"]

    def test_anchor_file_not_regular(self):
        fs = make_fs(not_regular={ANCHOR_PATH})
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["anchor-file-not-regular"]

    def test_chain_not_increasing(self):
        first = make_anchor(2, GENESIS, GENESIS)
        second = rehashed(make_anchor(2, "a" * 64, first["anchor_hash"],
                                      anchored_at="2026-09-17T00:01:00.000000+00:00"))
        fs = make_fs(anchor_bytes=jsonl(first, second))
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["anchor-sequence-not-increasing"]

    def test_chain_previous_link_mismatch(self):
        broken = rehashed({**SECOND, "previous_anchor_hash": FIRST["head_hash"]})
        fs = make_fs(anchor_bytes=jsonl(FIRST, broken))
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["anchor-previous-link-mismatch"]

    def test_chain_genesis_previous_violation(self):
        bad = rehashed(make_anchor(0, GENESIS, "f" * 64))
        fs = make_fs(anchor_bytes=jsonl(bad))
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["anchor-genesis-previous"]

    def test_chain_genesis_head_violation(self):
        bad = rehashed(make_anchor(0, "e" * 64, GENESIS))
        fs = make_fs(anchor_bytes=jsonl(bad))
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["anchor-genesis-head"]

    def test_chain_negative_sequence(self):
        bad = rehashed(make_anchor(-1, GENESIS, GENESIS))
        fs = make_fs(anchor_bytes=jsonl(bad))
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["anchor-sequence-negative"]

    def test_accepts_single_anchor_and_sequence_skips(self):
        # 单锚 + 跳号链（M14-42 契约：允许跳号不要求 +1）均通过
        for payload in (ONE_ANCHOR_BYTES, jsonl(FIRST, SECOND, THIRD)):
            fs = make_fs(anchor_bytes=payload)
            assert run(preflight_args(), fs) == 2  # 报告与锚不绑定 → 拒
            assert "verify-report-source-mismatch" in \
                codes(report_payload(fs, "preflight"))


# ---------------------------------------------------------------- verify 报告绑定链


class TestVerifyReportBinding:
    def test_preflight_happy_path(self):
        fs = make_fs()
        assert run(preflight_args(), fs) == 0
        payload = report_payload(fs, "preflight")
        assert payload["status"] == "pass"
        assert payload["problems"] == []
        assert payload["source"] == {
            "sha256": TWO_ANCHOR_SHA, "size_bytes": len(TWO_ANCHOR_BYTES),
            "anchor_count": 2, "first": anchor_fact(FIRST),
            "last": anchor_fact(SECOND)}
        assert payload["worm"] == {
            "endpoint_host": "127.0.0.1", "bucket": "audit-archive",
            "key": f"audit-anchor/{TWO_ANCHOR_SHA}/audit-anchor.jsonl",
            "version_id": "ver-0001", "retention_mode": "COMPLIANCE",
            "retain_until": "2036-09-17T19:10:00Z",
            "content_type": "application/x-ndjson",
            "object_sha256": TWO_ANCHOR_SHA,
            "object_size_bytes": len(TWO_ANCHOR_BYTES)}
        assert payload["verify_report"] == {
            "name": REPORT_PATH.name, "sha256": VERIFY_REPORT_SHA,
            "size_bytes": len(VERIFY_REPORT_BYTES)}
        assert payload["offline"]["root"] == str(OFFLINE_ROOT)
        assert payload["offline"]["layout"] == f"audit-anchor/{TWO_ANCHOR_SHA}"
        assert payload["offline"]["files"] == list(awoc.COPY_NAMES)
        assert payload["existing"] == {"state": "absent", "present": []}
        assert payload["offline_verified"] is False  # preflight 不宣称副本核验
        assert len(artifact_writes(fs)) == 3  # json + md + sidecar
        assert_zero_offline_writes(fs)

    def test_report_missing(self):
        fs = make_fs(report_bytes=None)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["verify-report-missing"]

    def test_report_symlink(self):
        fs = make_fs(symlinks={REPORT_PATH})
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["verify-report-symlink"]

    def test_report_not_regular(self):
        fs = make_fs(not_regular={REPORT_PATH})
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-not-regular"]

    def test_report_empty(self):
        fs = make_fs(report_bytes=b"")
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["verify-report-empty"]

    def test_report_not_json(self):
        fs = make_fs(report_bytes=b"\x00 not json")
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["verify-report-not-json"]

    def test_report_not_object(self):
        fs = make_fs(report_bytes=b"[1, 2]\n")
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-not-object"]

    def test_archive_command_report_rejected(self):
        # 上游 archive 报告不是可接受输入——本工具绑定 verify 通过事实
        data = mutated_report(lambda p: p.update(command="archive"))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-unrecognized"]

    def test_wrong_milestone_rejected(self):
        data = mutated_report(lambda p: p.update(milestone="M14-42"))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-unrecognized"]

    def test_wrong_tool_rejected(self):
        data = mutated_report(lambda p: p.update(tool="audit_worm_offline_copy"))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-unrecognized"]

    def test_status_fail_rejected(self):
        data = mutated_report(
            lambda p: (p.update(status="fail"),
                       p["problems"].append({"code": "x", "detail": "y"})))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["verify-report-not-pass"]

    def test_worm_verified_false_rejected(self):
        data = mutated_report(lambda p: p.update(worm_verified=False))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["verify-report-not-pass"]

    def test_nonempty_problems_rejected(self):
        data = mutated_report(
            lambda p: p["problems"].append({"code": "x", "detail": "y"}))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["verify-report-not-pass"]

    def test_source_block_mismatch(self):
        data = mutated_report(lambda p: p["source"].update(anchor_count=3))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-source-mismatch"]

    def test_cross_parameter_anchor_rejected(self):
        # 报告绑定的是另一份锚文件（单锚）——跨参数拒绝
        fs = make_fs(anchor_bytes=ONE_ANCHOR_BYTES)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-source-mismatch"]

    def test_key_mismatch(self):
        data = mutated_report(
            lambda p: p["target"].update(key="audit-anchor/other/audit-anchor.jsonl"))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-key-mismatch"]

    def test_missing_archive_block_rejected(self):
        data = mutated_report(lambda p: p.pop("archive"))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert "verify-report-incomplete" in codes(report_payload(fs, "preflight"))

    def test_empty_endpoint_host_rejected(self):
        data = mutated_report(lambda p: p["target"].update(endpoint_host=""))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-incomplete"]

    def test_empty_bucket_rejected(self):
        data = mutated_report(lambda p: p["target"].update(bucket=""))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-incomplete"]

    def test_empty_version_rejected(self):
        data = mutated_report(lambda p: p["archive"].update(version_id=""))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-incomplete"]

    def test_governance_retention_rejected(self):
        data = mutated_report(lambda p: p["archive"].update(retention_mode="GOVERNANCE"))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-retention-mode-mismatch"]

    @pytest.mark.parametrize("raw", ["2036-09-17T19:10:00", "not-a-date", "",
                                     "2036-13-40T99:00:00Z"])
    def test_retain_until_unparseable_rejected(self, raw):
        data = mutated_report(lambda p: p["archive"].update(retain_until=raw))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-retention-invalid"]

    def test_content_type_mismatch_rejected(self):
        data = mutated_report(lambda p: p["archive"].update(content_type="text/plain"))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-content-type-mismatch"]

    def test_object_sha_mismatch_rejected(self):
        data = mutated_report(
            lambda p: p["archive"].update(object_sha256="d" * 64))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-object-facts-mismatch"]

    def test_object_size_mismatch_rejected(self):
        data = mutated_report(lambda p: p["archive"].update(object_size_bytes=999))
        fs = make_fs(report_bytes=data)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["verify-report-object-facts-mismatch"]


# ---------------------------------------------------------------- 离线根/marker 契约


class TestOfflineRootAndMarker:
    def test_root_missing(self):
        fs = FakeFS(files={ANCHOR_PATH: TWO_ANCHOR_BYTES,
                           REPORT_PATH: VERIFY_REPORT_BYTES})
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["offline-root-missing"]

    def test_root_relative_rejected(self):
        fs = make_fs(root=OFFLINE_ROOT)
        assert run(preflight_args(root=Path("relative/offline-root")), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["offline-root-not-absolute"]

    def test_root_symlink_rejected(self):
        fs = make_fs(root=OFFLINE_ROOT, symlinks={OFFLINE_ROOT})
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["offline-root-symlink"]

    def test_root_not_directory(self):
        # 根路径上是个普通文件——不是目录
        fs = FakeFS(files={ANCHOR_PATH: TWO_ANCHOR_BYTES,
                           REPORT_PATH: VERIFY_REPORT_BYTES,
                           OFFLINE_ROOT: b"i am a file"})
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["offline-root-not-directory"]

    def test_root_ancestor_symlink_rejected(self):
        fs = make_fs(root=OFFLINE_ROOT, symlinks={OFFLINE_PARENT})
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["offline-root-ancestor-symlink"]

    def test_repo_root_itself_rejected(self):
        fs = make_fs(root=awoc.REPO_ROOT)
        assert run(preflight_args(root=awoc.REPO_ROOT), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["offline-root-inside-repo"]

    def test_verify_artifacts_dir_rejected(self):
        inside = awoc.REPO_ROOT / ".verify" / "artifacts" / "m14-49-inside"
        fs = make_fs(root=inside)
        assert run(preflight_args(root=inside), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["offline-root-inside-repo"]

    def test_repo_subdir_rejected(self):
        inside = awoc.REPO_ROOT / "tools" / "offline"
        fs = make_fs(root=inside)
        assert run(preflight_args(root=inside), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["offline-root-inside-repo"]

    def test_marker_missing(self):
        fs = make_fs(marker=False)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["marker-missing"]
        assert_zero_offline_writes(fs)

    @pytest.mark.parametrize("content", [
        b"", b"AIOS audit anchor WORM offline copy root v1",
        b"AIOS audit anchor WORM offline copy root v1\n\n",
        b"AIOS audit anchor WORM offline copy root v1\r\n",
        b"aios audit anchor worm offline copy root v1\n",
        b"AIOS audit anchor WORM offline copy root v2\n",
    ])
    def test_marker_content_mismatch(self, content):
        fs = make_fs(marker_content=content)
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["marker-content-mismatch"]

    def test_marker_symlink_rejected(self):
        fs = make_fs(symlinks={MARKER_PATH})
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["marker-symlink"]

    def test_marker_not_regular_rejected(self):
        fs = FakeFS(files={ANCHOR_PATH: TWO_ANCHOR_BYTES,
                           REPORT_PATH: VERIFY_REPORT_BYTES},
                    dirs={OFFLINE_ROOT, MARKER_PATH})
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == ["marker-not-regular"]


# ---------------------------------------------------------------- copy 语义


class TestCopy:
    def test_copy_happy_path(self):
        fs = make_fs()
        assert run(copy_args(), fs) == 0
        payload = report_payload(fs, "copy")
        assert payload["status"] == "pass"
        assert payload["copy"] == {"created": True, "existing_matched": False,
                                   "idempotent": False}
        assert payload["offline_verified"] is True
        paths = copy_paths()
        assert fs.read_bytes(paths["anchor"]) == TWO_ANCHOR_BYTES
        assert fs.read_bytes(paths["report"]) == VERIFY_REPORT_BYTES
        assert fs.read_bytes(paths["manifest"]) == expected_manifest_bytes()
        assert paths["dir"] in fs.created_dirs
        # marker 未被触碰
        assert fs.read_bytes(MARKER_PATH) == awoc.MARKER_CONTENT
        assert MARKER_PATH not in fs.writes
        # 写入面 = 离线三文件 + 报告三工件，别无其他
        assert sorted(offline_writes(fs)) == sorted(
            [paths["anchor"], paths["report"], paths["manifest"]])
        assert len(artifact_writes(fs)) == 3

    def test_copy_manifest_deterministic_and_complete(self):
        fs = make_fs()
        assert run(copy_args(), fs) == 0
        manifest = json.loads(fs.read_bytes(copy_paths()["manifest"])
                              .decode("utf-8"))
        assert manifest["schema_version"] == 1
        assert manifest["milestone"] == "M14-49"
        assert manifest["layout"] == f"audit-anchor/{TWO_ANCHOR_SHA}"
        assert manifest["anchor"]["sha256"] == TWO_ANCHOR_SHA
        assert manifest["verify_report"]["source_name"] == REPORT_PATH.name
        assert manifest["worm"]["version_id"] == "ver-0001"
        assert manifest["worm"]["retain_until"] == "2036-09-17T19:10:00Z"
        # 零时钟：同输入两次构造逐字节相同（幂等复算基础）
        assert expected_manifest_bytes() == expected_manifest_bytes(
            anchor_bytes=TWO_ANCHOR_BYTES, report_bytes=VERIFY_REPORT_BYTES,
            source_name=REPORT_PATH.name)

    def test_copy_idempotent_zero_overwrite(self):
        fs = make_fs()
        assert run(copy_args(), fs) == 0
        writes_after_first = list(fs.writes)
        assert run(copy_args(), fs) == 0
        payload = report_payload(fs, "copy", suffix="-2")
        assert payload["copy"] == {"created": False, "existing_matched": True,
                                   "idempotent": True}
        assert payload["offline_verified"] is True
        # 第二轮零离线写入（新增写入只有第二轮报告三工件）
        new_writes = [p for p in fs.writes if p not in writes_after_first]
        assert all(awoc.ARTIFACT_DIR == p or awoc.ARTIFACT_DIR in p.parents
                   for p in new_writes)
        assert len(new_writes) == 3

    def test_copy_existing_byte_mismatch_fail_closed(self):
        paths = copy_paths()
        fs = make_fs(offline_files={paths["anchor"]: TWO_ANCHOR_BYTES + b"x",
                                    paths["report"]: VERIFY_REPORT_BYTES,
                                    paths["manifest"]: expected_manifest_bytes()})
        assert run(copy_args(), fs) == 2
        assert codes(report_payload(fs, "copy")) == \
            ["offline-copy-byte-mismatch"]
        # 篡改字节被原样保留（零覆盖）
        assert fs.read_bytes(paths["anchor"]) == TWO_ANCHOR_BYTES + b"x"
        assert offline_writes(fs) == []

    def test_copy_partial_residue_refuses_overwrite(self):
        paths = copy_paths()
        fs = make_fs(offline_files={paths["manifest"]: expected_manifest_bytes()})
        assert run(copy_args(), fs) == 2
        assert codes(report_payload(fs, "copy")) == ["offline-copy-partial"]
        assert "manifest.json" in report_payload(fs, "copy")["problems"][0]["detail"]
        # 残留被原样保留
        assert fs.read_bytes(paths["manifest"]) == expected_manifest_bytes()
        assert offline_writes(fs) == []

    def test_copy_broken_symlink_target_rejected(self):
        # 目标位是悬空 symlink（exists 为 False）——仍拒绝且零写入
        paths = copy_paths()
        fs = make_fs(symlinks={paths["anchor"]})
        assert run(copy_args(), fs) == 2
        assert codes(report_payload(fs, "copy")) == ["offline-copy-symlink"]
        assert offline_writes(fs) == []

    def test_copy_layout_dir_symlink_rejected(self):
        paths = copy_paths()
        fs = make_fs(symlinks={paths["dir"]})
        assert run(copy_args(), fs) == 2
        assert codes(report_payload(fs, "copy")) == ["offline-copy-dir-symlink"]
        assert offline_writes(fs) == []

    def test_copy_layout_dir_not_regular_rejected(self):
        # 布局目录位置上是个普通文件
        paths = copy_paths()
        fs = make_fs(offline_files={paths["dir"]: b"blocked"})
        assert run(copy_args(), fs) == 2
        assert codes(report_payload(fs, "copy")) == \
            ["offline-copy-dir-not-regular"]
        assert offline_writes(fs) == []

    def test_copy_marker_missing_zero_writes(self):
        fs = make_fs(marker=False)
        assert run(copy_args(), fs) == 2
        assert codes(report_payload(fs, "copy")) == ["marker-missing"]
        assert offline_writes(fs) == []

    def test_copy_post_write_readback_drift_fail_closed(self):
        paths = copy_paths()
        fs = ReadBackCorruptFS(
            files={ANCHOR_PATH: TWO_ANCHOR_BYTES, REPORT_PATH: VERIFY_REPORT_BYTES,
                   MARKER_PATH: awoc.MARKER_CONTENT},
            dirs={OFFLINE_ROOT}, corrupt=paths["manifest"])
        assert run(copy_args(), fs) == 2
        assert codes(report_payload(fs, "copy")) == ["post-copy-byte-mismatch"]
        # 诚实边界：三文件已落盘（非单一事务）但工具如实报告漂移拒绝——
        # 工具不回滚不删除，后续运行按 offline-copy-byte-mismatch 拒绝
        assert sorted(offline_writes(fs)) == sorted(
            [paths["anchor"], paths["report"], paths["manifest"]])

    @pytest.mark.parametrize("phrase", [
        "execute audit anchor worm offline copy",
        "EXECUTE  AUDIT ANCHOR WORM OFFLINE COPY",
        "EXECUTE AUDIT ANCHOR WORM OFFLINE",
        "EXECUTE AUDIT ANCHOR WORM OFFLINE COPY ",
        " EXECUTE AUDIT ANCHOR WORM OFFLINE COPY",
        "EXECUTE AUDIT ANCHOR WORM ARCHIVE",
        "",
    ])
    def test_confirm_phrase_near_miss_zero_everything(self, phrase):
        fs = make_fs()
        assert run(copy_args(confirm=phrase), fs) == 2
        assert fs.writes == []  # 零报告、零离线写入
        assert not fs.created_dirs

    def test_confirm_flag_absent_zero_everything(self):
        fs = make_fs()
        assert run(base_args("copy"), fs) == 2
        assert fs.writes == []

    def test_copy_unexpected_write_failure_folds_secret(self, capsys):
        fs = ExplodingWriteFS(
            files={ANCHOR_PATH: TWO_ANCHOR_BYTES, REPORT_PATH: VERIFY_REPORT_BYTES,
                   MARKER_PATH: awoc.MARKER_CONTENT},
            dirs={OFFLINE_ROOT})
        assert run(copy_args(), fs) == 2
        assert fs.writes == []
        captured = capsys.readouterr()
        assert "unexpected OSError" in captured.err
        assert "Traceback" not in captured.err
        for secret in ("hunter2", "abc123", "password=", "api_key=", "bearer"):
            assert secret not in captured.out + captured.err


# ---------------------------------------------------------------- verify 只读重算


class TestVerifyCommand:
    def _copied_fs(self) -> FakeFS:
        fs = make_fs()
        assert run(copy_args(), fs) == 0
        return fs

    def test_verify_happy_after_copy(self):
        fs = self._copied_fs()
        writes_before = list(fs.writes)
        assert run(verify_args(), fs) == 0
        payload = report_payload(fs, "verify")
        assert payload["status"] == "pass"
        assert payload["offline_verified"] is True
        assert payload["existing"] == {
            "state": "matching",
            "present": list(awoc.COPY_NAMES)}
        # verify 零离线写入（新增只有报告工件）
        new_writes = [p for p in fs.writes if p not in writes_before]
        assert all(awoc.ARTIFACT_DIR == p or awoc.ARTIFACT_DIR in p.parents
                   for p in new_writes)

    def test_verify_absent_copy(self):
        fs = make_fs()
        assert run(verify_args(), fs) == 2
        payload = report_payload(fs, "verify")
        assert codes(payload) == ["offline-copy-missing"]
        assert payload["existing"]["state"] == "absent"
        assert_zero_offline_writes(fs)

    def test_verify_tampered_anchor_copy(self):
        paths = copy_paths()
        fs = make_fs(offline_files={paths["anchor"]: TWO_ANCHOR_BYTES + b"z",
                                    paths["report"]: VERIFY_REPORT_BYTES,
                                    paths["manifest"]: expected_manifest_bytes()})
        assert run(verify_args(), fs) == 2
        assert codes(report_payload(fs, "verify")) == \
            ["offline-copy-byte-mismatch"]
        assert fs.read_bytes(paths["anchor"]) == TWO_ANCHOR_BYTES + b"z"

    def test_verify_tampered_report_copy(self):
        paths = copy_paths()
        fs = make_fs(offline_files={paths["anchor"]: TWO_ANCHOR_BYTES,
                                    paths["report"]: b"tampered",
                                    paths["manifest"]: expected_manifest_bytes()})
        assert run(verify_args(), fs) == 2
        assert codes(report_payload(fs, "verify")) == \
            ["offline-copy-byte-mismatch"]

    def test_verify_tampered_manifest(self):
        paths = copy_paths()
        fs = make_fs(offline_files={paths["anchor"]: TWO_ANCHOR_BYTES,
                                    paths["report"]: VERIFY_REPORT_BYTES,
                                    paths["manifest"]: b"{}"})
        assert run(verify_args(), fs) == 2
        assert codes(report_payload(fs, "verify")) == \
            ["offline-copy-byte-mismatch"]

    def test_verify_missing_manifest_partial(self):
        paths = copy_paths()
        fs = make_fs(offline_files={paths["anchor"]: TWO_ANCHOR_BYTES,
                                    paths["report"]: VERIFY_REPORT_BYTES})
        assert run(verify_args(), fs) == 2
        assert codes(report_payload(fs, "verify")) == ["offline-copy-partial"]

    def test_verify_copy_file_swapped_to_symlink(self):
        paths = copy_paths()
        fs = make_fs(offline_files={paths["report"]: VERIFY_REPORT_BYTES,
                                    paths["manifest"]: expected_manifest_bytes()},
                     symlinks={paths["anchor"]})
        assert run(verify_args(), fs) == 2
        assert codes(report_payload(fs, "verify")) == ["offline-copy-symlink"]

    def test_verify_detects_cross_parameter_report_name(self):
        # 副本以 REPORT_PATH 复制；verify 换 REPORT2_PATH（同字节异名）——
        # manifest 的 source_name 绑定使跨参数核验必然失败
        fs = make_fs()
        fs.files[REPORT2_PATH] = VERIFY_REPORT_BYTES
        assert run(copy_args(), fs) == 0
        assert run(verify_args(report=REPORT2_PATH), fs) == 2
        assert codes(report_payload(fs, "verify")) == \
            ["offline-copy-byte-mismatch"]

    def test_verify_never_writes_offline_root(self):
        fs = self._copied_fs()
        assert run(verify_args(), fs) == 0
        assert run(verify_args(), fs) == 0
        # 两轮 verify 都零离线写入
        assert offline_writes(fs) == [copy_paths()["anchor"],
                                      copy_paths()["report"],
                                      copy_paths()["manifest"]]


# ---------------------------------------------------------------- preflight 既有副本观察


class TestPreflightExistingState:
    def test_preflight_matching_existing_passes(self):
        fs = make_fs()
        assert run(copy_args(), fs) == 0
        assert run(preflight_args(), fs) == 0
        payload = report_payload(fs, "preflight")
        assert payload["existing"] == {"state": "matching",
                                       "present": list(awoc.COPY_NAMES)}
        assert payload["problems"] == []

    def test_preflight_partial_existing_fails(self):
        paths = copy_paths()
        fs = make_fs(offline_files={paths["anchor"]: TWO_ANCHOR_BYTES})
        assert run(preflight_args(), fs) == 2
        payload = report_payload(fs, "preflight")
        assert codes(payload) == ["offline-copy-partial"]
        assert payload["existing"]["state"] == "invalid"
        assert payload["existing"]["present"] == ["audit-anchor.jsonl"]

    def test_preflight_mismatch_existing_fails(self):
        paths = copy_paths()
        fs = make_fs(offline_files={
            paths["anchor"]: TWO_ANCHOR_BYTES,
            paths["report"]: VERIFY_REPORT_BYTES,
            paths["manifest"]: b"tampered manifest"})
        assert run(preflight_args(), fs) == 2
        assert codes(report_payload(fs, "preflight")) == \
            ["offline-copy-byte-mismatch"]


# ---------------------------------------------------------------- 报告工件与防碰撞


class TestReportArtifacts:
    def test_all_three_commands_write_json_md_sidecar(self):
        fs = make_fs()
        assert run(preflight_args(), fs) == 0
        assert run(copy_args(), fs) == 0
        assert run(verify_args(), fs) == 0
        # 报告名按 command 前缀作用域（{command}-{stamp}-{token}）：
        # 三命令各跑一次互不撞名，全部零后缀；-N 递增仅同命令撞名时
        # 触发（见 test_name_collision_increments_and_preserves_original）
        suffixes = {"preflight": "", "copy": "", "verify": ""}
        for mode, suffix in suffixes.items():
            json_path = artifact_path(mode, ".json", suffix)
            md_path = artifact_path(mode, ".md", suffix)
            sha_path = artifact_path(mode, ".json.sha256", suffix)
            # 断言最终工件存在与字节（不依赖 fs.writes 的中间记录语义）
            for p in (json_path, md_path, sha_path):
                assert p in fs.files
            sidecar = fs.read_bytes(sha_path).decode("utf-8")
            digest, _, name = sidecar.partition("  ")
            assert name.strip() == f"{mode}-{STAMP}-{TOKEN}{suffix}.json"
            assert digest == hashlib.sha256(
                fs.read_bytes(json_path)).hexdigest()
            markdown = fs.read_bytes(md_path).decode("utf-8")
            assert "M14-49" in markdown
            assert mode in markdown

    def test_sidecar_matches_for_failing_report_too(self):
        fs = make_fs(marker=False)
        assert run(preflight_args(), fs) == 2
        json_path = artifact_path("preflight", ".json")
        sidecar = fs.read_bytes(artifact_path("preflight", ".json.sha256")) \
            .decode("utf-8")
        digest = sidecar.split()[0]
        assert digest == hashlib.sha256(fs.read_bytes(json_path)).hexdigest()
        payload = report_payload(fs, "preflight")
        assert payload["status"] == "fail"

    def test_name_collision_increments_and_preserves_original(self):
        stale_name = f"copy-{STAMP}-{TOKEN}.md"
        stale_bytes = b"stale orphan markdown\n"
        fs = make_fs()
        fs.files[awoc.ARTIFACT_DIR / stale_name] = stale_bytes
        assert run(copy_args(), fs) == 0
        # 新报告换 -2 名；孤儿工件原样保留
        payload = report_payload(fs, "copy", suffix="-2")
        assert payload["status"] == "pass"
        assert fs.read_bytes(awoc.ARTIFACT_DIR / stale_name) == stale_bytes
        assert artifact_path("copy", ".json", "-2") in fs.writes

    def test_report_records_offline_root_and_no_secrets(self):
        fs = make_fs()
        assert run(copy_args(), fs) == 0
        json_text = fs.read_bytes(artifact_path("copy", ".json")).decode("utf-8")
        md_text = fs.read_bytes(artifact_path("copy", ".md")).decode("utf-8")
        # 契约：报告记录用户显式传入的原始绝对路径字符串（可复现）。
        # JSON 结构化断言——raw 文本子串在 Windows 会因 JSON 反斜杠
        # 转义（\ → \\）而失配，不代表实现漂移
        payload = json.loads(json_text)
        assert payload["offline"]["root"] == str(OFFLINE_ROOT)
        # Markdown 纯文本原样渲染 root，子串成立
        assert str(OFFLINE_ROOT) in md_text
        for secret in ("password=", "api_key=", "Bearer ", "access_key"):
            assert secret not in json_text
            assert secret not in md_text

    def test_cli_missing_required_args_exit_2(self):
        fs = make_fs()
        with pytest.raises(SystemExit) as excinfo:
            awoc.main(["preflight", "--anchor-file", str(ANCHOR_PATH)],
                      fs=fs, clock=FakeClock(), suffix_gen=FakeSuffixGen())
        assert excinfo.value.code == 2

    def test_cli_no_command_rejected(self):
        fs = make_fs()
        assert awoc.main([], fs=fs, clock=FakeClock(),
                         suffix_gen=FakeSuffixGen()) == 2
        assert fs.writes == []

    def test_cli_unknown_command_rejected(self):
        fs = make_fs()
        with pytest.raises(SystemExit):
            awoc.main(["bogus"], fs=fs, clock=FakeClock(),
                      suffix_gen=FakeSuffixGen())


# ---------------------------------------------------------------- RealFS 真实盘


class TestRealFS:
    def test_write_bytes_atomic_preserves_exact_bytes(self, tmp_path):
        real = awoc.RealFS()
        target = tmp_path / "binary-copy.bin"
        payloads = [TWO_ANCHOR_BYTES, b"crlf\r\nlines\r\n", b"\x00\xff\xfe binary"]
        for data in payloads:
            real.write_bytes_atomic(target, data)
            assert target.read_bytes() == data
        assert not (tmp_path / "binary-copy.bin.tmp").exists()

    def test_write_text_atomic_writes_utf8_bytes(self, tmp_path):
        real = awoc.RealFS()
        target = tmp_path / "report.json"
        real.write_text_atomic(target, '{"ok": true, "中文": "注"}\n')
        raw = target.read_bytes()
        assert raw.endswith(b"\n")
        assert b"\r" not in raw
        assert "中文".encode() in raw

    def test_real_end_to_end_copy_then_verify(self, tmp_path, monkeypatch):
        # 报告工件重定向到 tmp（绝不污染仓库 .verify）
        artifact_dir = tmp_path / "artifacts"
        monkeypatch.setattr(awoc, "ARTIFACT_DIR", artifact_dir)

        anchor_file = tmp_path / "audit-anchor.jsonl"
        anchor_file.write_bytes(TWO_ANCHOR_BYTES)
        report_file = tmp_path / "verify-20260917.json"
        report_file.write_bytes(VERIFY_REPORT_BYTES)

        offline_root = tmp_path / "offline-media"
        offline_root.mkdir()
        (offline_root / awoc.MARKER_NAME).write_bytes(awoc.MARKER_CONTENT)

        real = awoc.RealFS()
        base = ["--anchor-file", str(anchor_file),
                "--verify-report", str(report_file),
                "--offline-root", str(offline_root)]
        rc_copy = awoc.main(["copy", *base, "--confirm", awoc.CONFIRM_COPY],
                            fs=real, clock=FakeClock(),
                            suffix_gen=FakeSuffixGen())
        assert rc_copy == 0

        dir_path = offline_root / "audit-anchor" / TWO_ANCHOR_SHA
        assert (dir_path / "audit-anchor.jsonl").read_bytes() == TWO_ANCHOR_BYTES
        copied_report = (dir_path / "verify-report.json").read_bytes()
        assert copied_report == VERIFY_REPORT_BYTES
        assert b"\r" not in copied_report  # Windows 行尾不翻译
        assert (dir_path / "manifest.json").read_bytes() == \
            expected_manifest_bytes(source_name=report_file.name)

        rc_verify = awoc.main(["verify", *base], fs=real, clock=FakeClock(),
                              suffix_gen=FakeSuffixGen())
        assert rc_verify == 0
        # 幂等重跑同样成功
        rc_again = awoc.main(["copy", *base, "--confirm", awoc.CONFIRM_COPY],
                             fs=real, clock=FakeClock(),
                             suffix_gen=FakeSuffixGen())
        assert rc_again == 0

    def test_real_fs_rejects_repo_inside_root(self, tmp_path):
        # 真实 resolve 语义下仓库内路径仍拒绝（仓库根本身是真实存在目录）
        fs = awoc.RealFS()
        report = {"problems": []}
        assert awoc._offline_root_gate(fs, str(awoc.REPO_ROOT), report) is None
        assert report["problems"][0]["code"] == "offline-root-inside-repo"
