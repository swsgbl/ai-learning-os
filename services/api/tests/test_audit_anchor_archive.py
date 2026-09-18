r"""M14-43 契约：audit anchor WORM/对象锁归档工具（零真实网络 / 零真实 S3）。

被测对象 ``tools/ops/audit_anchor_archive.py``（单文件纯标准库 + boto3 懒
导入；S3Client/FS/Clock/env 全注入）。全部用 FakeS3/FakeFS 锁行为：

- preflight：本地 anchor JSONL 完整校验（字段集/类型/canonical JSON
  anchor_hash 重算/sequence 非负且严格递增——与 M14-42 生产端同契约，
  允许跳号不要求 +1/previous-anchor 链接/sequence 0 行 head 必须是
  genesis 常量/anchored_at 必须可按 ISO-8601 解析/symlink 源拒绝/残缺行
  拒绝/空文件拒绝）；endpoint 策略（公网必须 HTTPS，loopback/RFC1918
  可 HTTP，userinfo/scheme 拒绝）；凭据只来自环境变量（缺失 → 零 S3
  调用）；bucket 存在 + versioning Enabled + Object Lock enabled 任一
  不满足 → 写入前 fail-closed；preflight 对 S3 零写操作；
- archive：确认短语精确匹配（近似 → 零执行零报告）；retention 只认
  COMPLIANCE + tz-aware 严格未来 retain-until（fractional 秒合法——报告
  canonical UTC 表示保留非零微秒，archive→verify 精确往返）；内容寻址
  key ``audit-anchor/<sha256>/audit-anchor.jsonl``；新对象恰好一次
  put_object（COMPLIANCE + retain-until + checksum + x-ndjson + 显式
  ``if_none_match="*"`` 条件创建——head 判不存在后被并发抢占时服务端
  拒绝，fail-closed 绝不覆盖竞态写入）；已存在
  且字节匹配 → 核验 retention 后不覆盖零 put；已存在字节不匹配 →
  fail-closed；put 后重读字节/元数据任何不匹配（含 size/content-type
  漂移）→ fail-closed；
- verify：重复本地校验 + bucket WORM preflight + 对象逐字节 SHA-256 +
  version ID 与归档报告记录值精确一致（真实适配器按记录 version 定向
  head/get）+ COMPLIANCE + retain-until + content-type + size 精确核验；
  归档报告伴生 .sha256 sidecar 与报告字节必须一致（sidecar 空/非
  UTF-8/畸形 → exit 2 而非异常）；报告必须绑定当前调用事实（status/
  worm_verified/source/endpoint host/bucket/key/hash/size/retention/
  content-type），跨 bucket/endpoint 或失败归档报告一律拒绝；
- 报告：原子写 gitignored .verify/artifacts/m14-43-audit-worm-archive/
  三命令（preflight/archive/verify）统一三工件——JSON + Markdown +
  字节精确 ``.json.sha256`` sidecar（sha256sum 形态；M14-58 修复前仅
  archive 写 sidecar，verify/preflight 报告缺伴生摘要，M14-55 更新器
  按 worm-sidecar-missing fail-closed；RealFS 以字节落盘，Windows 不做
  \n→\r\n 转换，sidecar 哈希与盘上字节一致），报告名防碰撞——每份
  报告名带 ``suffix_gen()`` 生成的 CSPRNG 随机后缀（默认
  ``secrets.token_hex``，32 hex chars = 128 bits，同 command 同秒并发
  撞名概率约 2**-128），存在性探测循环（含残留孤儿工件如只有 .md）
  保留为 fail-closed 兜底、递增 ``-2``/``-3`` 换名，
  绝不覆盖既有报告证据，endpoint 只记 host（绝不完整 endpoint）、绝不含 access
  key/secret 值，worm_verified 布尔如实（preflight=false：未验证具体
  对象）；报告/文件系统意外异常 fail-closed exit 2，绝不上抛原始
  traceback 或秘密形态值；
- 真实适配器边界（零网络 stub 客户端）：AWS ChecksumSHA256 在
  boto3 适配器边界从 hex 转 base64；head/get 按记录 version 传
  VersionId；
- 源级契约：无 delete/overwrite/bucket 配置 API、无凭据 CLI 选项、无
  无关工具的生产常量、boto3 仅懒导入（函数体内）。
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "tools" / "ops" / "audit_anchor_archive.py"

STAMP = "20260917-050000"
# 注入的固定报告名随机后缀（32 hex chars，与真随机同宽）——测试不依赖
# 真随机即可断言确定性的报告工件名
TOKEN = "5f3a91c2b7d84e60a1f3c29d4e7b8a6f"
NOW = datetime(2026, 9, 17, 5, 0, 0, tzinfo=UTC)
RETAIN_UNTIL = "2036-01-01T00:00:00Z"
RETAIN_DT = datetime(2036, 1, 1, tzinfo=UTC)
OTHER_RETAIN_DT = datetime(2046, 1, 1, tzinfo=UTC)
FRACTIONAL_RETAIN = "2036-01-01T00:00:00.250000Z"
FRACTIONAL_RETAIN_DT = datetime(2036, 1, 1, 0, 0, 0, 250000, tzinfo=UTC)
BUCKET = "audit-archive"
ENDPOINT_LOCAL = "http://127.0.0.1:9000"
ANCHOR_PATH = Path("/opt/audit/audit-anchor.jsonl")
GENESIS = "0" * 64


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


awa = _load_module(SCRIPT, "audit_anchor_archive_under_test")

GENESIS = awa.GENESIS_PREVIOUS_HASH  # 与被测工具同一常量值，锚点语义见下方独立构造
REPORT_JSON = awa.ARTIFACT_DIR / f"archive-{STAMP}-{TOKEN}.json"
REPORT_MD = awa.ARTIFACT_DIR / f"archive-{STAMP}-{TOKEN}.md"
REPORT_SHA = awa.ARTIFACT_DIR / f"archive-{STAMP}-{TOKEN}.json.sha256"
PREFLIGHT_JSON = awa.ARTIFACT_DIR / f"preflight-{STAMP}-{TOKEN}.json"

GOOD_ENV = {awa.ENV_ACCESS_KEY: "test-access-key-value",
            awa.ENV_SECRET_KEY: "test-secret-key-value"}


# ---------------------------------------------------------------- 锚点构造（独立实现）


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _anchor_hash(anchor: dict) -> str:
    payload = {k: anchor[k] for k in anchor if k != "anchor_hash"}
    return hashlib.sha256(_canonical(payload)).hexdigest()


def make_anchor(sequence: int, head_hash: str, previous: str,
                anchored_at: str = "2026-09-17T00:00:00.000000+00:00") -> dict:
    anchor = {"schema_version": 1, "algorithm": "sha256", "sequence": sequence,
              "head_hash": head_hash, "anchored_at": anchored_at,
              "previous_anchor_hash": previous}
    anchor["anchor_hash"] = _anchor_hash(anchor)
    return anchor


def jsonl(*anchors: dict, newline: bool = True) -> bytes:
    data = b"".join(_canonical(a) + b"\n" for a in anchors)
    return data if newline else data.rstrip(b"\n")


FIRST = make_anchor(0, GENESIS, GENESIS)
SECOND = make_anchor(1, "a" * 64, FIRST["anchor_hash"],
                     anchored_at="2026-09-17T01:00:00.000000+00:00")
TWO_ANCHOR_BYTES = jsonl(FIRST, SECOND)
TWO_ANCHOR_SHA = hashlib.sha256(TWO_ANCHOR_BYTES).hexdigest()
ONE_ANCHOR_BYTES = jsonl(FIRST)
KEY = f"audit-anchor/{TWO_ANCHOR_SHA}/audit-anchor.jsonl"


# ---------------------------------------------------------------- fakes


class FakeClock:
    def now(self) -> datetime:
        return NOW

    def utc_now_iso(self) -> str:
        return "2026-09-17T05:00:00Z"

    def stamp(self) -> str:
        return STAMP


class FakeSuffixGen:
    """suffix_gen 注入替身：按序发令牌，耗尽后重复最后一个。

    报告名随机后缀生成器可注入——测试不依赖真随机即可构造确定性
    报告名（固定单令牌）或模拟并发进程各拿不同随机后缀（多令牌）。
    """

    def __init__(self, tokens: list[str] | None = None) -> None:
        self.tokens = list(tokens or [TOKEN])
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return self.tokens[min(self.calls - 1, len(self.tokens) - 1)]


class FakeFS:
    def __init__(self, files=None, symlinks=None, not_regular=None) -> None:
        self.files: dict[Path, bytes] = dict(files or {})
        self.symlinks = set(symlinks or ())
        self.not_regular = set(not_regular or ())
        self.writes: list[Path] = []
        self.created_dirs: list[Path] = []

    def exists(self, path) -> bool:
        candidate = Path(path)
        return candidate in self.files or candidate in self.not_regular

    def is_file(self, path) -> bool:
        candidate = Path(path)
        return candidate in self.files and candidate not in self.not_regular

    def is_symlink(self, path) -> bool:
        return Path(path) in self.symlinks

    def read_bytes(self, path) -> bytes:
        return self.files[Path(path)]

    def write_text_atomic(self, path, text: str) -> None:
        target = Path(path)
        self.files[target] = text.encode("utf-8")
        self.writes.append(target)

    def mkdirs(self, path) -> None:
        self.created_dirs.append(Path(path))


@dataclass
class StoredObject:
    body: bytes
    version_id: str = "ver-existing"
    object_lock_mode: str | None = awa.RETENTION_MODE_COMPLIANCE
    retain_until: datetime | None = RETAIN_DT
    content_type: str | None = awa.CONTENT_TYPE


class FakeS3:
    """S3Client 注入替身：记录全部调用；override 刻画后验漂移。

    - head_override/get_override：恒生效（verify 场景）；
    - post_put_head/post_put_get：仅在发生过 put 之后生效（archive 后验
      漂移场景——存在性 head 不受影响）。
    """

    def __init__(self, *, bucket_exists: bool = True,
                 versioning: str | None = "Enabled",
                 object_lock: bool = True,
                 objects: dict[str, StoredObject] | None = None,
                 head_override=None, get_override: bytes | None = None,
                 post_put_head=None, post_put_get: bytes | None = None) -> None:
        self.bucket_exists = bucket_exists
        self.versioning = versioning
        self.object_lock = object_lock
        self.objects: dict[str, StoredObject] = dict(objects or {})
        self.head_override = head_override
        self.get_override = get_override
        self.post_put_head = post_put_head
        self.post_put_get = post_put_get
        self.calls: list[tuple[str, ...]] = []
        self.put_calls: list[dict] = []

    def _post_put(self) -> bool:
        return bool(self.put_calls)

    def head_bucket(self, *, bucket: str) -> None:
        self.calls.append(("head_bucket", bucket))
        if not self.bucket_exists:
            raise awa.S3Error("s3-bucket-not-found")

    def get_bucket_versioning(self, *, bucket: str) -> str | None:
        self.calls.append(("get_bucket_versioning", bucket))
        return self.versioning

    def get_object_lock_configuration(self, *, bucket: str) -> bool:
        self.calls.append(("get_object_lock_configuration", bucket))
        return self.object_lock

    def head_object(self, *, bucket: str, key: str, version_id: str | None = None):
        # 单版本宽松替身：记录 version_id 供断言，不模拟多版本语义
        # （版本一致性由被测工具的 head.version_id 比对负责）
        self.calls.append(("head_object", bucket, key, version_id))
        if self._post_put() and self.post_put_head is not None:
            return self.post_put_head
        if self.head_override is not None:
            return self.head_override
        obj = self.objects.get(key)
        if obj is None:
            return None
        return awa.ObjectHead(version_id=obj.version_id,
                              object_lock_mode=obj.object_lock_mode,
                              retain_until=obj.retain_until,
                              content_type=obj.content_type,
                              size=len(obj.body))

    def get_object(self, *, bucket: str, key: str,
                   version_id: str | None = None) -> bytes:
        self.calls.append(("get_object", bucket, key, version_id))
        if self._post_put() and self.post_put_get is not None:
            return self.post_put_get
        if self.get_override is not None:
            return self.get_override
        obj = self.objects.get(key)
        if obj is None:
            raise awa.S3Error("s3-object-not-found")
        return obj.body

    def put_object(self, *, bucket: str, key: str, body: bytes,
                   content_type: str, checksum_sha256: str,
                   object_lock_mode: str, retain_until: datetime,
                   if_none_match: str | None = None) -> str:
        self.calls.append(("put_object", bucket, key))
        self.put_calls.append({
            "bucket": bucket, "key": key, "body": body,
            "content_type": content_type, "checksum_sha256": checksum_sha256,
            "object_lock_mode": object_lock_mode, "retain_until": retain_until,
            "if_none_match": if_none_match,
        })
        # 条件创建语义（模拟服务端 IfNoneMatch="*" 前置条件）：key 已
        # 存在时拒绝创建——head 说不存在之后、put 之前被并发抢占的场景
        if if_none_match == "*" and key in self.objects:
            raise awa.S3Error("s3-conditional-create-conflict")
        version_id = f"ver-{len(self.objects)}"
        self.objects[key] = StoredObject(
            body=body, version_id=version_id,
            object_lock_mode=object_lock_mode,
            retain_until=retain_until, content_type=content_type)
        return version_id


def make_fs(*, anchor_bytes: bytes | None = TWO_ANCHOR_BYTES,
            extra: dict[Path, bytes] | None = None,
            symlinks: set[Path] | tuple[Path, ...] = (),
            not_regular: set[Path] | tuple[Path, ...] = ()) -> FakeFS:
    files: dict[Path, bytes] = {}
    if anchor_bytes is not None:
        files[ANCHOR_PATH] = anchor_bytes
    files.update(extra or {})
    return FakeFS(files, symlinks=set(symlinks), not_regular=set(not_regular))


def preflight_args(endpoint: str = ENDPOINT_LOCAL,
                   anchor=ANCHOR_PATH) -> list[str]:
    return ["preflight", "--anchor-file", str(anchor),
            "--endpoint", endpoint, "--bucket", BUCKET]


def archive_args(*, endpoint: str = ENDPOINT_LOCAL,
                 retain_until: str = RETAIN_UNTIL,
                 mode: str = "COMPLIANCE",
                 confirm: str | None = None) -> list[str]:
    args = ["archive", "--anchor-file", str(ANCHOR_PATH),
            "--endpoint", endpoint, "--bucket", BUCKET,
            "--retain-until", retain_until, "--retention-mode", mode,
            "--confirm", confirm if confirm is not None
            else awa.CONFIRM_ARCHIVE]
    return args


def verify_args(endpoint: str = ENDPOINT_LOCAL,
                report: str = f"archive-{STAMP}-{TOKEN}.json") -> list[str]:
    return ["verify", "--anchor-file", str(ANCHOR_PATH),
            "--endpoint", endpoint, "--bucket", BUCKET,
            "--report", report]


def run(args, *, s3=None, fs=None, env=None, suffix_gen=None):
    fake_s3 = s3 if s3 is not None else FakeS3()
    fake_fs = fs if fs is not None else make_fs()
    rc = awa.main(args, s3=fake_s3, fs=fake_fs, clock=FakeClock(),
                  env=env if env is not None else GOOD_ENV,
                  suffix_gen=suffix_gen if suffix_gen is not None
                  else FakeSuffixGen())
    return rc, fake_s3, fake_fs


def puts(s3: FakeS3) -> list[tuple[str, ...]]:
    return [c for c in s3.calls if c[0] == "put_object"]


def report_payload(fs: FakeFS, mode: str) -> dict:
    path = awa.ARTIFACT_DIR / f"{mode}-{STAMP}-{TOKEN}.json"
    assert path in fs.files, f"report not written: {path}"
    return json.loads(fs.files[path].decode("utf-8"))


def codes(payload: dict) -> list[str]:
    return [str(p.get("code")) for p in payload.get("problems", [])]


def run_archive_then_verify(*, s3=None, fs=None, verify_report=None):
    """archive 腿 + verify 腿共享同一 S3 替身与调用日志。

    返回 (verify_rc, s3, fs, puts_after_archive)：puts_after_archive 是
    archive 腿结束时的 put 计数——verify 的只读性按「verify 不新增 put」
    断言，而不是错误的「全程零 put」（archive 合法执行过一次 put）。
    """
    fake_s3 = s3 if s3 is not None else FakeS3()
    fake_fs = fs if fs is not None else make_fs()
    rc_archive = awa.main(archive_args(), s3=fake_s3, fs=fake_fs,
                          clock=FakeClock(), env=GOOD_ENV,
                          suffix_gen=FakeSuffixGen())
    assert rc_archive == 0
    puts_after_archive = len(fake_s3.put_calls)
    rc = awa.main(verify_args(report=verify_report
                              or f"archive-{STAMP}-{TOKEN}.json"),
                  s3=fake_s3, fs=fake_fs, clock=FakeClock(), env=GOOD_ENV,
                  suffix_gen=FakeSuffixGen())
    return rc, fake_s3, fake_fs, puts_after_archive


# ---------------------------------------------------------------- 常量与源级契约


def test_constants_locked() -> None:
    assert awa.CONFIRM_ARCHIVE == "EXECUTE AUDIT ANCHOR WORM ARCHIVE"
    assert awa.ARTIFACT_DIR == (awa.REPO_ROOT / ".verify" / "artifacts"
                                 / "m14-43-audit-worm-archive")
    assert awa.ENV_ACCESS_KEY == "AIOS_AUDIT_ARCHIVE_ACCESS_KEY"
    assert awa.ENV_SECRET_KEY == "AIOS_AUDIT_ARCHIVE_SECRET_KEY"
    assert awa.CONTENT_TYPE == "application/x-ndjson"
    assert awa.RETENTION_MODE_COMPLIANCE == "COMPLIANCE"
    assert awa.GENESIS_PREVIOUS_HASH == "0" * 64
    assert awa.object_key(TWO_ANCHOR_SHA) == KEY
    assert awa.EXIT_OK == 0 and awa.EXIT_REJECT == 2


def test_source_has_no_delete_or_overwrite_apis() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in ("delete_object", "delete_bucket", "copy_object",
                  "create_bucket", "put_bucket_versioning",
                  "put_object_lock_configuration", "put_bucket_acl",
                  "put_object_acl"):
        assert token not in source, token


def test_source_has_no_credential_cli_options() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in ("--access-key", "--secret-key", "--password", "--token",
                  "aws_access_key_id=", "aws_secret_access_key="):
        assert token not in source, token


def test_source_has_no_unrelated_production_constants() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for token in ("aios-m14-03-production-rehearsal", "env.production-recovery",
                  "RELEASE.2025-10-15", "MINIO_ROOT_PASSWORD", "docker"):
        assert token not in source, token


def test_source_boto3_import_is_lazy() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "import boto3" in source  # 真实执行适配器存在
    for line in source.splitlines():
        if "import boto3" in line:
            assert line.startswith((" ", "\t")), "boto3 必须函数体内懒导入"


def test_redact_secrets_shapes() -> None:
    assert "hunter2secret99" not in awa.redact_secrets("password=hunter2secret99\n")
    assert "tok_abcdef123456" not in awa.redact_secrets("Bearer tok_abcdef123456")
    assert awa.redact_secrets("正常文本") == "正常文本"


# ---------------------------------------------------------------- preflight：本地校验


def test_preflight_passes_on_valid_two_anchor_chain() -> None:
    rc, s3, fs = run(preflight_args())
    assert rc == 0
    assert puts(s3) == []  # preflight 零写操作
    payload = report_payload(fs, "preflight")
    assert payload["status"] == "pass"
    assert payload["source"]["sha256"] == TWO_ANCHOR_SHA
    assert payload["source"]["anchor_count"] == 2
    assert payload["worm_verified"] is False  # preflight 不验证具体对象——如实
    names = [c[0] for c in s3.calls]
    assert "head_bucket" in names
    assert "get_bucket_versioning" in names
    assert "get_object_lock_configuration" in names


def test_preflight_accepts_genesis_single_anchor() -> None:
    rc, s3, _ = run(preflight_args(), fs=make_fs(anchor_bytes=ONE_ANCHOR_BYTES))
    assert rc == 0
    assert puts(s3) == []


@pytest.mark.parametrize("anchor_bytes", [
    b'{"not json\n',
    b'{"schema_version": 1}\n',
    jsonl(dict(FIRST, extra_field="x")),
    jsonl({k: v for k, v in FIRST.items() if k != "anchored_at"}),
    jsonl(dict(FIRST, anchor_hash="b" * 64)),
    jsonl(dict(FIRST, schema_version=2)),
    jsonl(dict(FIRST, algorithm="md5")),
    jsonl(FIRST, dict(SECOND, sequence=0)),
    jsonl(dict(FIRST, head_hash="a" * 64, anchor_hash=""),),
    jsonl(FIRST, SECOND, make_anchor(1, "b" * 64, FIRST["anchor_hash"])),
    b"\n",
    TWO_ANCHOR_BYTES.rstrip(b"\n"),
    b"",
    b'{"schema_version": 1, "algorithm": "sha256", "sequence": true, '
    b'"head_hash": "' + GENESIS.encode() + b'", "anchored_at": "x", '
    b'"previous_anchor_hash": "' + GENESIS.encode() + b'"}\n',
])
def test_preflight_rejects_malformed_anchor_sources(anchor_bytes: bytes) -> None:
    rc, s3, _ = run(preflight_args(), fs=make_fs(anchor_bytes=anchor_bytes))
    assert rc == 2
    assert s3.calls == []  # 本地校验失败 → 零 S3 访问


# ---------------- M14-42 生产端契约：sequence 非负严格递增（允许跳号）+ anchored_at ISO-8601


@pytest.mark.parametrize("anchor_bytes", [
    # 跳号但严格递增 + 链接正确 → 合法（不要求 +1 连续）
    jsonl(FIRST, make_anchor(5, "a" * 64, FIRST["anchor_hash"])),
    jsonl(FIRST, make_anchor(2, "a" * 64, FIRST["anchor_hash"]),
          make_anchor(9, "b" * 64, make_anchor(
              2, "a" * 64, FIRST["anchor_hash"])["anchor_hash"])),
    # 首锚不必是 sequence=0（锚文件从 DB 已有进度开始锚定是合法形态）
    jsonl(make_anchor(7, "a" * 64, GENESIS)),
], ids=["gap-0-to-5", "gaps-2-9", "first-anchor-not-zero"])
def test_preflight_accepts_strictly_increasing_sequence_gaps(
        anchor_bytes: bytes) -> None:
    rc, s3, _ = run(preflight_args(), fs=make_fs(anchor_bytes=anchor_bytes))
    assert rc == 0
    assert puts(s3) == []


@pytest.mark.parametrize("anchor_bytes", [
    jsonl(make_anchor(-3, GENESIS, GENESIS)),          # 负 sequence
    jsonl(FIRST, make_anchor(-1, "a" * 64, FIRST["anchor_hash"])),
    jsonl(FIRST, make_anchor(0, "a" * 64, FIRST["anchor_hash"])),  # 回退到 0
    jsonl(FIRST, SECOND, make_anchor(                 # 重复 sequence
        1, "b" * 64, SECOND["anchor_hash"])),
], ids=["negative-genesis", "negative-second", "decrease", "duplicate"])
def test_preflight_rejects_bad_sequences(anchor_bytes: bytes) -> None:
    rc, s3, _ = run(preflight_args(), fs=make_fs(anchor_bytes=anchor_bytes))
    assert rc == 2
    assert s3.calls == []


@pytest.mark.parametrize("anchored_at", [
    "not-a-timestamp", "2026-09-17T99:99:99+00:00", "",
], ids=["garbage", "impossible-time", "empty"])
def test_preflight_rejects_non_iso8601_anchored_at(anchored_at: str) -> None:
    bad = make_anchor(0, GENESIS, GENESIS, anchored_at=anchored_at)
    rc, s3, _ = run(preflight_args(), fs=make_fs(anchor_bytes=jsonl(bad)))
    assert rc == 2
    assert s3.calls == []


def test_preflight_accepts_iso8601_anchored_at_forms() -> None:
    aware = make_anchor(0, GENESIS, GENESIS,
                        anchored_at="2026-09-17T00:00:00+00:00")
    micro = make_anchor(1, "a" * 64, aware["anchor_hash"],
                        anchored_at="2026-09-17T01:00:00.123456+00:00")
    rc, _, _ = run(preflight_args(),
                   fs=make_fs(anchor_bytes=jsonl(aware, micro)))
    assert rc == 0


def test_preflight_rejects_symlink_anchor_source() -> None:
    rc, s3, _ = run(preflight_args(), fs=make_fs(symlinks={ANCHOR_PATH}))
    assert rc == 2
    assert s3.calls == []


def test_preflight_rejects_missing_anchor_file() -> None:
    rc, s3, _ = run(preflight_args(), fs=make_fs(anchor_bytes=None))
    assert rc == 2
    assert s3.calls == []


def test_preflight_rejects_non_regular_anchor_source() -> None:
    rc, s3, _ = run(preflight_args(), fs=make_fs(not_regular={ANCHOR_PATH}))
    assert rc == 2
    assert s3.calls == []


def test_preflight_rejects_broken_previous_link() -> None:
    broken = make_anchor(1, "a" * 64, "c" * 64)
    rc, s3, _ = run(preflight_args(), fs=make_fs(anchor_bytes=jsonl(FIRST, broken)))
    assert rc == 2
    assert s3.calls == []


# ---------------------------------------------------------------- preflight：endpoint 策略


@pytest.mark.parametrize("endpoint", [
    "https://s3.example.com",
    "https://minio.corp.example:9000",
    "http://127.0.0.1:9000",
    "http://localhost:9000",
    "http://[::1]:9000",
    "http://10.1.2.3:9000",
    "http://172.16.5.4:9000",
    "http://192.168.1.10:9000",
])
def test_preflight_accepts_allowed_endpoints(endpoint: str) -> None:
    rc, s3, _ = run(preflight_args(endpoint=endpoint))
    assert rc == 0
    assert puts(s3) == []


@pytest.mark.parametrize("endpoint", [
    "",
    "  ",
    "http://minio.example.com:9000",  # 公网 HTTP
    "ftp://127.0.0.1:9000",
    "file:///etc/passwd",
    "https://ak:sk@s3.example.com",  # userinfo 内嵌凭据
    "http://",
])
def test_preflight_rejects_disallowed_endpoints(endpoint: str) -> None:
    rc, s3, _ = run(preflight_args(endpoint=endpoint))
    assert rc == 2
    assert s3.calls == []  # endpoint 策略先于任何 S3 访问


# ---------------------------------------------------------------- preflight：凭据与 bucket


@pytest.mark.parametrize("env", [
    {},
    {awa.ENV_SECRET_KEY: "test-secret-key-value"},
    {awa.ENV_ACCESS_KEY: "test-access-key-value"},
], ids=["both-missing", "access-missing", "secret-missing"])
def test_preflight_rejects_missing_credentials_zero_s3_calls(env) -> None:
    rc, s3, _ = run(preflight_args(), env=env)
    assert rc == 2
    assert s3.calls == []


def test_preflight_rejects_missing_bucket() -> None:
    rc, s3, _ = run(preflight_args(), s3=FakeS3(bucket_exists=False))
    assert rc == 2
    assert puts(s3) == []


@pytest.mark.parametrize("versioning", [None, "Suspended", ""],
                         ids=["none", "suspended", "empty"])
def test_preflight_rejects_versioning_not_enabled(versioning) -> None:
    rc, s3, _ = run(preflight_args(), s3=FakeS3(versioning=versioning))
    assert rc == 2
    assert puts(s3) == []


def test_preflight_rejects_object_lock_not_enabled() -> None:
    rc, s3, _ = run(preflight_args(), s3=FakeS3(object_lock=False))
    assert rc == 2
    assert puts(s3) == []


@pytest.mark.parametrize("make_bad_s3", [
    lambda: FakeS3(bucket_exists=False),
    lambda: FakeS3(versioning="Suspended"),
    lambda: FakeS3(object_lock=False),
])
def test_archive_preflight_failures_never_put(make_bad_s3) -> None:
    bad = make_bad_s3()
    rc, s3, _ = run(archive_args(), s3=bad)
    assert rc == 2
    assert puts(s3) == []


# ---------------------------------------------------------------- archive：确认短语与参数门


@pytest.mark.parametrize("phrase", [
    "", "EXECUTE AUDIT ANCHOR WORM ARCHIVE NOW",
    "execute audit anchor worm archive",
    "EXECUTE AUDIT ANCHOR WORM ARCHIV",
    "EXECUTE  AUDIT ANCHOR WORM ARCHIVE",
    "EXECUTE AUDIT ANCHOR WORM ARCHIVE ",
])
def test_archive_rejects_nonexact_confirm_zero_everything(phrase: str) -> None:
    args = archive_args()
    if phrase:
        args[args.index("--confirm") + 1] = phrase
    else:
        i = args.index("--confirm")
        args = args[:i] + args[i + 2:]
    rc, s3, fs = run(args)
    assert rc == 2
    assert s3.calls == []
    assert fs.writes == []  # 近似短语 → 连报告都不写


@pytest.mark.parametrize("mode", ["GOVERNANCE", "governance", "COMPLIANCE ", ""])
def test_archive_rejects_non_compliance_mode(mode: str) -> None:
    rc, s3, fs = run(archive_args(mode=mode))
    assert rc == 2
    assert s3.calls == []
    assert fs.writes == []


@pytest.mark.parametrize("retain", [
    "2020-01-01T00:00:00Z",       # 过去
    "2026-09-17T05:00:00Z",       # 等于 now（不严格未来）
    "2036-01-01T00:00:00",        # naive 无时区
    "not-a-timestamp",
    "",
])
def test_archive_rejects_bad_retain_until(retain: str) -> None:
    rc, s3, _ = run(archive_args(retain_until=retain))
    assert rc == 2
    assert s3.calls == []


# ---------------------------------------------------------------- archive：写入路径


def test_archive_new_object_single_put_with_compliance_lock() -> None:
    rc, s3, _ = run(archive_args())
    assert rc == 0
    assert len(s3.put_calls) == 1
    put = s3.put_calls[0]
    assert put["bucket"] == BUCKET
    assert put["key"] == KEY
    assert put["body"] == TWO_ANCHOR_BYTES
    assert put["content_type"] == "application/x-ndjson"
    assert put["checksum_sha256"] == TWO_ANCHOR_SHA
    assert put["object_lock_mode"] == "COMPLIANCE"
    assert put["retain_until"] == RETAIN_DT
    # 新对象 put 是条件创建：显式 if_none_match 哨兵经注入协议传递
    assert put["if_none_match"] == awa.IF_NONE_MATCH_CREATE == "*"
    # 顺序：WORM preflight 与存在性检查先于 put；put 后重读字节+元数据
    names = [c[0] for c in s3.calls]
    assert names.index("get_object_lock_configuration") < names.index("put_object")
    assert names.index("head_object") < names.index("put_object")
    assert names.index("get_object") > names.index("put_object")
    assert names.index("head_object", names.index("put_object")) > names.index("put_object")
    # 存储侧事实
    stored = s3.objects[KEY]
    assert stored.body == TWO_ANCHOR_BYTES
    assert stored.object_lock_mode == "COMPLIANCE"


def test_archive_report_records_facts_and_sidecar() -> None:
    rc, _, fs = run(archive_args())
    assert rc == 0
    payload = report_payload(fs, "archive")
    assert payload["schema_version"] == 1
    assert payload["milestone"] == "M14-43"
    assert payload["status"] == "pass"
    assert payload["started_at_utc"] and payload["ended_at_utc"]
    assert payload["source"] == {
        "sha256": TWO_ANCHOR_SHA, "size_bytes": len(TWO_ANCHOR_BYTES),
        "anchor_count": 2,
        "first": {"sequence": 0, "head_hash": GENESIS,
                  "anchor_hash": FIRST["anchor_hash"]},
        "last": {"sequence": 1, "head_hash": "a" * 64,
                 "anchor_hash": SECOND["anchor_hash"]},
    }
    assert payload["target"] == {"endpoint_host": "127.0.0.1",
                                 "bucket": BUCKET, "key": KEY}
    archive = payload["archive"]
    assert archive["created"] is True
    assert archive["existing_matched"] is False
    assert archive["version_id"] == "ver-0"
    assert archive["retention_mode"] == "COMPLIANCE"
    assert archive["retain_until"] == "2036-01-01T00:00:00Z"
    assert archive["object_sha256"] == TWO_ANCHOR_SHA
    assert archive["object_size_bytes"] == len(TWO_ANCHOR_BYTES)
    assert archive["content_type"] == "application/x-ndjson"
    assert payload["worm_verified"] is True
    assert payload["problems"] == []
    # 三工件：JSON + MD + .sha256 sidecar（sidecar 必须匹配报告字节）
    assert REPORT_JSON in fs.writes and REPORT_MD in fs.writes
    assert REPORT_SHA in fs.writes
    sidecar = fs.files[REPORT_SHA].decode("utf-8").split()
    assert sidecar[0] == hashlib.sha256(fs.files[REPORT_JSON]).hexdigest()


def test_archive_existing_matching_object_not_overwritten() -> None:
    s3 = FakeS3(objects={KEY: StoredObject(body=TWO_ANCHOR_BYTES)})
    rc, s3, fs = run(archive_args(), s3=s3)
    assert rc == 0
    assert puts(s3) == []  # 绝不覆盖已存在归档对象
    payload = report_payload(fs, "archive")
    assert payload["archive"]["created"] is False
    assert payload["archive"]["existing_matched"] is True
    assert payload["archive"]["version_id"] == "ver-existing"
    assert payload["worm_verified"] is True


# ---------------- fractional retain-until：canonical 表示保留非零微秒


def test_utc_z_round_trips_fractional_microseconds() -> None:
    """_utc_z 非零微秒必须保留且与 _parse_iso_utc 精确往返（截断即漂移）。"""
    assert awa._utc_z(datetime(2036, 1, 1, tzinfo=UTC)) == \
        "2036-01-01T00:00:00Z"
    frac = FRACTIONAL_RETAIN_DT
    rendered = awa._utc_z(frac)
    assert rendered == "2036-01-01T00:00:00.250000Z"
    assert awa._parse_iso_utc(rendered) == frac  # 精确往返
    # 任意输入时区 canonical 化后微秒仍在
    tokyo = frac.astimezone(timezone(timedelta(hours=9)))
    assert awa._utc_z(tokyo) == "2036-01-01T00:00:00.250000Z"
    assert awa._utc_z(datetime(2036, 1, 1, 0, 0, 0, 1, tzinfo=UTC)) == \
        "2036-01-01T00:00:00.000001Z"


def test_archive_then_verify_with_fractional_retain_until() -> None:
    """聚焦回归：fractional-second retain-until 的 archive→verify 全链路。

    曾缺陷：archive.retain_until canonical 表示截断微秒 → verify 复算的
    报告记录值与对象真实 retain_until 不一致 → 误报
    verify-object-retain-until-mismatch。
    """
    fs = make_fs()
    s3 = FakeS3()
    rc_archive = awa.main(archive_args(retain_until=FRACTIONAL_RETAIN),
                          s3=s3, fs=fs, clock=FakeClock(), env=GOOD_ENV,
                          suffix_gen=FakeSuffixGen())
    assert rc_archive == 0
    # put 请求携带完整微秒精度
    assert s3.put_calls[0]["retain_until"] == FRACTIONAL_RETAIN_DT
    assert s3.objects[KEY].retain_until == FRACTIONAL_RETAIN_DT
    # 报告 canonical 表示保留非零微秒（不截断）
    payload = report_payload(fs, "archive")
    assert payload["archive"]["retain_until"] == "2036-01-01T00:00:00.250000Z"
    # verify 复算：报告记录值与对象 retain_until 精确一致 → pass
    rc = awa.main(verify_args(), s3=s3, fs=fs, clock=FakeClock(),
                  env=GOOD_ENV, suffix_gen=FakeSuffixGen())
    assert rc == 0
    v_payload = report_payload(fs, "verify")
    assert v_payload["status"] == "pass"
    assert v_payload["worm_verified"] is True
    assert v_payload["archive"]["retain_until"] == \
        "2036-01-01T00:00:00.250000Z"


# ---------------- 条件创建竞态：head 判不存在后被并发抢占 → fail-closed


class RacingFakeS3(FakeS3):
    """存在性 head 永远返回「不存在」，但对象实际已在——竞态形态替身。

    刻画场景：另一写入者在 head 之后、put 之前抢先创建了同 key 对象。
    条件创建 ``if_none_match="*"`` 必须被拒绝（FakeS3 模拟服务端前置
    条件），本工具 fail-closed 绝不覆盖竞态写入者。
    """

    def head_object(self, *, bucket: str, key: str,
                    version_id: str | None = None):
        # 存在性探测被竞态者「骗过」——隐式返回 None（判「不存在」）
        self.calls.append(("head_object", bucket, key, version_id))


def test_archive_race_after_absent_head_fails_closed() -> None:
    """head 判不存在后被并发抢占：条件创建被拒 → exit 2 绝不覆盖。"""
    s3 = RacingFakeS3(objects={KEY: StoredObject(body=b"racer wrote first")})
    rc, s3, fs = run(archive_args(), s3=s3)
    assert rc == 2
    # 恰好一次被拒的 put 尝试，且携带条件创建哨兵
    assert len(s3.put_calls) == 1
    assert s3.put_calls[0]["if_none_match"] == "*"
    # 竞态写入者的字节原样保留——本工具绝不覆盖已写入对象
    assert s3.objects[KEY].body == b"racer wrote first"
    assert "s3-put-failed" in codes(report_payload(fs, "archive"))


def test_archive_existing_byte_mismatch_fails_closed() -> None:
    s3 = FakeS3(objects={KEY: StoredObject(body=b"different bytes")})
    rc, s3, fs = run(archive_args(), s3=s3)
    assert rc == 2
    assert puts(s3) == []
    payload = report_payload(fs, "archive")
    assert "existing-object-byte-mismatch" in codes(payload)


@pytest.mark.parametrize("stored", [
    StoredObject(body=TWO_ANCHOR_BYTES, object_lock_mode=None),
    StoredObject(body=TWO_ANCHOR_BYTES, object_lock_mode="GOVERNANCE"),
    StoredObject(body=TWO_ANCHOR_BYTES, retain_until=None),
    StoredObject(body=TWO_ANCHOR_BYTES, retain_until=OTHER_RETAIN_DT),
    StoredObject(body=TWO_ANCHOR_BYTES, version_id=""),
    StoredObject(body=TWO_ANCHOR_BYTES, content_type="application/json"),
    StoredObject(body=TWO_ANCHOR_BYTES, content_type=None),
], ids=["mode-none", "mode-governance", "retain-none", "retain-differs",
        "version-empty", "content-type-json", "content-type-none"])
def test_archive_existing_retention_mismatch_fails_closed(stored) -> None:
    s3 = FakeS3(objects={KEY: stored})
    rc, s3, _ = run(archive_args(), s3=s3)
    assert rc == 2
    assert puts(s3) == []


# ---------------------------------------------------------------- archive：put 后核验


@pytest.mark.parametrize("post_head,post_get,code", [
    (awa.ObjectHead(version_id="ver-x", object_lock_mode="COMPLIANCE",
                    retain_until=RETAIN_DT, content_type=awa.CONTENT_TYPE,
                    size=len(TWO_ANCHOR_BYTES)),
     b"tampered bytes", "post-put-byte-mismatch"),
    (awa.ObjectHead(version_id="ver-x", object_lock_mode="COMPLIANCE",
                    retain_until=RETAIN_DT, content_type=awa.CONTENT_TYPE,
                    size=len(b"tampered bytes")),
     TWO_ANCHOR_BYTES, "post-put-version-mismatch"),
    (awa.ObjectHead(version_id="ver-0", object_lock_mode="GOVERNANCE",
                    retain_until=RETAIN_DT, content_type=awa.CONTENT_TYPE,
                    size=len(TWO_ANCHOR_BYTES)),
     TWO_ANCHOR_BYTES, "post-put-retention-mode-mismatch"),
    (awa.ObjectHead(version_id="ver-0", object_lock_mode="COMPLIANCE",
                    retain_until=OTHER_RETAIN_DT, content_type=awa.CONTENT_TYPE,
                    size=len(TWO_ANCHOR_BYTES)),
     TWO_ANCHOR_BYTES, "post-put-retain-until-mismatch"),
    (awa.ObjectHead(version_id="ver-0", object_lock_mode="COMPLIANCE",
                    retain_until=RETAIN_DT, content_type="text/plain",
                    size=len(TWO_ANCHOR_BYTES)),
     TWO_ANCHOR_BYTES, "post-put-content-type-mismatch"),
    (awa.ObjectHead(version_id="ver-0", object_lock_mode="COMPLIANCE",
                    retain_until=RETAIN_DT, content_type=awa.CONTENT_TYPE,
                    size=len(TWO_ANCHOR_BYTES) + 7),
     TWO_ANCHOR_BYTES, "post-put-size-mismatch"),
])
def test_archive_post_put_drift_fails_closed(post_head, post_get, code) -> None:
    s3 = FakeS3(post_put_head=post_head, post_put_get=post_get)
    fs = make_fs()
    rc, s32, fs2 = run(archive_args(), s3=s3, fs=fs)
    assert rc == 2
    assert len(s32.put_calls) == 1  # put 已发生（一次），后验发现漂移
    payload = report_payload(fs2, "archive")
    assert code in codes(payload)


# ---------------------------------------------------------------- verify


def test_verify_happy_path_matches_archive_report() -> None:
    rc, s3, fs, puts_after_archive = run_archive_then_verify()
    assert rc == 0
    assert puts_after_archive == 1  # archive 腿恰好一次合法 put
    # verify 只读：以 archive 腿结束后的 put 计数为基线，verify 不新增 put
    assert len(s3.put_calls) == puts_after_archive
    payload = report_payload(fs, "verify")
    assert payload["status"] == "pass"
    assert payload["worm_verified"] is True
    assert payload["archive_report"]["sha256"] == hashlib.sha256(
        fs.files[REPORT_JSON]).hexdigest()
    assert payload["archive"]["version_id"] == "ver-0"
    assert payload["archive"]["retention_mode"] == "COMPLIANCE"
    assert payload["archive"]["retain_until"] == "2036-01-01T00:00:00Z"
    assert payload["archive"]["content_type"] == "application/x-ndjson"
    assert payload["archive"]["object_size_bytes"] == len(TWO_ANCHOR_BYTES)
    # verify 的 head/get 按归档报告记录的 version 定向读取
    heads = [c for c in s3.calls if c[0] == "head_object"]
    gets = [c for c in s3.calls if c[0] == "get_object"]
    assert heads[-1][3] == "ver-0"
    assert gets[-1][3] == "ver-0"


def test_verify_report_paired_with_matching_sidecar() -> None:
    """M14-58 回归：verify 成功报告原子配对字节精确的 sidecar。

    曾缺陷：``_write_report`` 仅对 archive 命令写 ``.json.sha256``
    sidecar，verify 报告缺伴生摘要 → M14-55 更新器对真实 verify 报告
    按 ``worm-sidecar-missing`` fail-closed 拒绝。修复后三命令统一三
    工件；sidecar 必须与所写报告字节精确一致（sha256sum 形态
    ``digest␣␣name.json\\n``）。
    """
    rc, _, fs, _ = run_archive_then_verify()
    assert rc == 0
    report_json = awa.ARTIFACT_DIR / f"verify-{STAMP}-{TOKEN}.json"
    report_sha = awa.ARTIFACT_DIR / f"verify-{STAMP}-{TOKEN}.json.sha256"
    assert report_json in fs.writes and report_sha in fs.writes
    digest = hashlib.sha256(fs.files[report_json]).hexdigest()
    assert fs.files[report_sha] == (
        f"{digest}  verify-{STAMP}-{TOKEN}.json\n").encode()


def test_failed_verify_report_also_paired_with_matching_sidecar() -> None:
    """M14-58 回归：拒绝路径的 verify 报告同样三工件齐全。

    失败证据也必须可被摘要校验（fail-closed 报告不是二等证据）——
    无归档报告 → exit 2，但落盘报告仍须配对字节精确的 sidecar。
    """
    fs = make_fs()  # 无归档报告 → verify fail-closed exit 2
    rc, _, fs2 = run(verify_args(), s3=FakeS3(), fs=fs)
    assert rc == 2
    report_json = awa.ARTIFACT_DIR / f"verify-{STAMP}-{TOKEN}.json"
    report_sha = awa.ARTIFACT_DIR / f"verify-{STAMP}-{TOKEN}.json.sha256"
    assert report_json in fs2.writes and report_sha in fs2.writes
    digest = hashlib.sha256(fs2.files[report_json]).hexdigest()
    assert fs2.files[report_sha] == (
        f"{digest}  verify-{STAMP}-{TOKEN}.json\n").encode()


def test_failed_archive_report_also_paired_with_matching_sidecar() -> None:
    """M14-58 回归：拒绝路径的 archive 报告同样三工件齐全。

    与失败 verify 对称（fail-closed 报告不是二等证据）——已存在对象
    字节不符 → exit 2，但落盘报告仍须配对字节精确的 sidecar。
    """
    s3 = FakeS3(objects={KEY: StoredObject(body=b"different bytes")})
    rc, _, fs = run(archive_args(), s3=s3)
    assert rc == 2
    assert puts(s3) == []
    report_json = awa.ARTIFACT_DIR / f"archive-{STAMP}-{TOKEN}.json"
    report_sha = awa.ARTIFACT_DIR / f"archive-{STAMP}-{TOKEN}.json.sha256"
    assert report_json in fs.writes and report_sha in fs.writes
    digest = hashlib.sha256(fs.files[report_json]).hexdigest()
    assert fs.files[report_sha] == (
        f"{digest}  archive-{STAMP}-{TOKEN}.json\n").encode()


def test_verify_targets_recorded_version_on_head_and_get() -> None:
    rc, s3, _, _ = run_archive_then_verify()
    assert rc == 0
    heads = [c for c in s3.calls if c[0] == "head_object"]
    gets = [c for c in s3.calls if c[0] == "get_object"]
    # archive 腿：存在性探测（最新版本，未定向）+ put 后核验（定向新版本）
    assert heads[0][3] is None
    assert heads[1][3] == "ver-0"
    assert gets[0][3] == "ver-0"
    # verify 腿：按归档报告记录的 version 定向 head/get
    assert heads[-1][3] == "ver-0"
    assert gets[-1][3] == "ver-0"


def test_verify_version_mismatch_with_report_fails() -> None:
    fs = make_fs()
    s3 = FakeS3()
    assert run(archive_args(), s3=s3, fs=fs)[0] == 0
    s3.objects[KEY].version_id = "ver-other"  # 对象 version 与报告不符
    rc, _, fs2 = run(verify_args(), s3=s3, fs=fs)
    assert rc == 2
    assert "verify-object-version-mismatch" in codes(
        report_payload(fs2, "verify"))


def test_verify_missing_object_fails() -> None:
    fs = make_fs()
    s3 = FakeS3()
    rc_archive, _, _ = run(archive_args(), s3=s3, fs=fs)
    assert rc_archive == 0
    s3.objects.pop(KEY)  # 对象消失
    rc, _, _ = run(verify_args(), s3=s3, fs=fs)
    assert rc == 2


def test_verify_byte_mismatch_fails() -> None:
    fs = make_fs()
    s3 = FakeS3()
    assert run(archive_args(), s3=s3, fs=fs)[0] == 0
    s3.objects[KEY].body = b"replaced bytes"  # 同 key 不同字节
    rc, _, _ = run(verify_args(), s3=s3, fs=fs)
    assert rc == 2


@pytest.mark.parametrize("mutate", [
    lambda o: setattr(o, "object_lock_mode", "GOVERNANCE"),
    lambda o: setattr(o, "object_lock_mode", None),
    lambda o: setattr(o, "retain_until", OTHER_RETAIN_DT),
    lambda o: setattr(o, "retain_until", None),
    lambda o: setattr(o, "version_id", ""),
    lambda o: setattr(o, "content_type", "application/json"),
    lambda o: setattr(o, "content_type", None),
], ids=["governance", "mode-none", "retain-differs", "retain-none",
        "version-empty", "content-json", "content-none"])
def test_verify_retention_or_version_drift_fails(mutate) -> None:
    fs = make_fs()
    s3 = FakeS3()
    assert run(archive_args(), s3=s3, fs=fs)[0] == 0
    mutate(s3.objects[KEY])
    rc, _, _ = run(verify_args(), s3=s3, fs=fs)
    assert rc == 2


def test_verify_head_reports_missing_version_fails() -> None:
    fs = make_fs()
    s3 = FakeS3()
    assert run(archive_args(), s3=s3, fs=fs)[0] == 0
    s3.head_override = awa.ObjectHead(
        version_id="", object_lock_mode="COMPLIANCE",
        retain_until=RETAIN_DT, content_type=awa.CONTENT_TYPE,
        size=len(TWO_ANCHOR_BYTES))
    rc, _, _ = run(verify_args(), s3=s3, fs=fs)
    assert rc == 2


def test_verify_head_size_mismatch_with_read_bytes_fails() -> None:
    fs = make_fs()
    s3 = FakeS3()
    assert run(archive_args(), s3=s3, fs=fs)[0] == 0
    s3.head_override = awa.ObjectHead(
        version_id="ver-0", object_lock_mode="COMPLIANCE",
        retain_until=RETAIN_DT, content_type=awa.CONTENT_TYPE,
        size=len(TWO_ANCHOR_BYTES) + 3)  # 元数据 size 与回读字节数不符
    rc, _, fs2 = run(verify_args(), s3=s3, fs=fs)
    assert rc == 2
    assert "verify-object-size-mismatch" in codes(
        report_payload(fs2, "verify"))


def test_verify_tampered_archive_report_fails() -> None:
    fs = make_fs()
    s3 = FakeS3()
    assert run(archive_args(), s3=s3, fs=fs)[0] == 0
    payload = json.loads(fs.files[REPORT_JSON].decode("utf-8"))
    payload["archive"]["version_id"] = "ver-tampered"
    fs.files[REPORT_JSON] = json.dumps(payload).encode("utf-8")
    rc, _, _ = run(verify_args(), s3=s3, fs=fs)
    assert rc == 2  # sidecar 与报告字节不再一致


def test_verify_anchor_file_changed_after_archive_fails() -> None:
    fs = make_fs()
    s3 = FakeS3()
    assert run(archive_args(), s3=s3, fs=fs)[0] == 0
    fs.files[ANCHOR_PATH] = jsonl(FIRST, SECOND, make_anchor(
        2, "c" * 64, SECOND["anchor_hash"]))  # 本地文件已前进
    rc, _, _ = run(verify_args(), s3=s3, fs=fs)
    assert rc == 2  # 重算 key ≠ 报告记录 key


def test_verify_missing_report_fails() -> None:
    fs = make_fs()
    rc, _, _ = run(verify_args(), s3=FakeS3(), fs=fs)  # 无归档报告
    assert rc == 2


def test_verify_report_outside_artifact_dir_fails(tmp_path) -> None:
    evil = tmp_path / "evil-report.json"
    fs = make_fs(extra={evil: b"{}"})
    rc, _, _ = run(verify_args(report=str(evil)), s3=FakeS3(), fs=fs)
    assert rc == 2


def test_verify_rejects_local_anchor_drift_before_s3() -> None:
    fs = make_fs(anchor_bytes=b"garbage\n")
    rc, s3, _ = run(verify_args(), s3=FakeS3(), fs=fs)
    assert rc == 2
    assert s3.calls == []


# ---------------- verify：归档报告绑定当前调用事实（跨 bucket/endpoint/失败报告拒绝）


def resign_report(fs: FakeFS, mutate) -> None:
    """改写归档报告后重签 sidecar（构造 sidecar 一致但事实不同的报告）。"""
    payload = json.loads(fs.files[REPORT_JSON].decode("utf-8"))
    mutate(payload)
    data = json.dumps(payload, indent=2, ensure_ascii=False,
                      sort_keys=True).encode("utf-8") + b"\n"
    fs.files[REPORT_JSON] = data
    fs.files[REPORT_SHA] = (
        f"{hashlib.sha256(data).hexdigest()}"
        f"  archive-{STAMP}-{TOKEN}.json\n"
    ).encode()


@pytest.mark.parametrize("mutate,code", [
    (lambda p: p["target"].__setitem__("bucket", "other-bucket"),
     "verify-archive-report-target-mismatch"),
    (lambda p: p["target"].__setitem__("endpoint_host", "evil.example.com"),
     "verify-archive-report-target-mismatch"),
    (lambda p: p.__setitem__("status", "fail"),
     "verify-archive-report-not-pass"),
    (lambda p: p.__setitem__("worm_verified", False),
     "verify-archive-report-not-pass"),
    (lambda p: p["source"].__setitem__("sha256", "f" * 64),
     "verify-archive-report-source-mismatch"),
    (lambda p: p["source"].__setitem__("anchor_count", 9),
     "verify-archive-report-source-mismatch"),
    (lambda p: p["archive"].__setitem__("object_size_bytes", 1),
     "verify-archive-report-archive-facts-mismatch"),
    (lambda p: p["archive"].__setitem__("retention_mode", "GOVERNANCE"),
     "verify-archive-report-archive-facts-mismatch"),
    (lambda p: p["archive"].__setitem__("object_sha256", "e" * 64),
     "verify-archive-report-archive-facts-mismatch"),
    (lambda p: p["archive"].__setitem__("content_type", "text/plain"),
     "verify-archive-report-archive-facts-mismatch"),
], ids=["other-bucket", "other-endpoint", "status-fail", "worm-false",
        "source-sha", "source-count", "object-size", "retention-mode",
        "object-sha", "content-type"])
def test_verify_rejects_report_not_bound_to_invocation(mutate, code) -> None:
    fs = make_fs()
    s3 = FakeS3()
    assert run(archive_args(), s3=s3, fs=fs)[0] == 0
    resign_report(fs, mutate)
    rc, _, fs2 = run(verify_args(), s3=s3, fs=fs)
    assert rc == 2
    assert code in codes(report_payload(fs2, "verify"))


def test_verify_rejects_failed_archive_report() -> None:
    fs = make_fs()
    # put 后漂移 → 归档失败报告（status=fail，无 archive 事实，worm_verified=false）
    s3 = FakeS3(post_put_get=b"tampered bytes")
    assert run(archive_args(), s3=s3, fs=fs)[0] == 2
    rc, _, fs2 = run(verify_args(), s3=FakeS3(), fs=fs)
    assert rc == 2
    assert "verify-archive-report-not-pass" in codes(
        report_payload(fs2, "verify"))


# ---------------- verify：sidecar fail-closed（空/非 UTF-8/畸形 → exit 2 非异常）


@pytest.mark.parametrize("sidecar_bytes", [
    b"",
    b"\xff\xfe garbage\n",
    b"garbage-no-digest\n",
    b"deadbeef  archive.json\n",
    b"\n\n",
], ids=["empty", "not-utf8", "no-digest", "short-digest", "blank"])
def test_verify_malformed_sidecar_fails_closed(sidecar_bytes) -> None:
    fs = make_fs()
    s3 = FakeS3()
    assert run(archive_args(), s3=s3, fs=fs)[0] == 0
    fs.files[REPORT_SHA] = sidecar_bytes
    rc, _, fs2 = run(verify_args(), s3=s3, fs=fs)  # 不得抛异常
    assert rc == 2
    assert "verify-archive-report-sidecar-invalid" in codes(
        report_payload(fs2, "verify"))


def test_verify_wrong_but_wellformed_sidecar_digest_fails() -> None:
    fs = make_fs()
    s3 = FakeS3()
    assert run(archive_args(), s3=s3, fs=fs)[0] == 0
    fs.files[REPORT_SHA] = \
        f"{'0' * 64}  archive-{STAMP}-{TOKEN}.json\n".encode()
    rc, _, fs2 = run(verify_args(), s3=s3, fs=fs)
    assert rc == 2
    assert "verify-archive-report-sidecar-mismatch" in codes(
        report_payload(fs2, "verify"))


# ---------------------------------------------------------------- 报告脱敏


def test_report_redacts_endpoint_to_host() -> None:
    rc, _, fs = run(archive_args(endpoint="https://s3.example.com:9000"))
    assert rc == 0
    payload = report_payload(fs, "archive")
    assert payload["target"]["endpoint_host"] == "s3.example.com"
    for path in (REPORT_JSON, REPORT_MD):
        text = fs.files[path].decode("utf-8")
        assert "https://" not in text
        assert "http://" not in text
        assert ":9000" not in text


def test_reports_contain_no_secret_values() -> None:
    rc, _, fs = run(archive_args())
    assert rc == 0
    for path in (REPORT_JSON, REPORT_MD, REPORT_SHA):
        text = fs.files[path].decode("utf-8")
        assert "test-access-key-value" not in text
        assert "test-secret-key-value" not in text
        assert awa.ENV_ACCESS_KEY not in text
        assert awa.ENV_SECRET_KEY not in text


def test_preflight_report_written_in_artifact_dir() -> None:
    rc, _, fs = run(preflight_args())
    assert rc == 0
    assert PREFLIGHT_JSON in fs.writes
    assert PREFLIGHT_JSON.parent == awa.ARTIFACT_DIR
    assert awa.ARTIFACT_DIR.match("*/.verify/artifacts/m14-43-audit-worm-archive") \
        or str(awa.ARTIFACT_DIR).endswith("m14-43-audit-worm-archive")


def test_preflight_report_paired_with_matching_sidecar() -> None:
    """M14-58 回归：preflight 报告同样原子配对字节精确的 sidecar。"""
    rc, _, fs = run(preflight_args())
    assert rc == 0
    assert PREFLIGHT_JSON in fs.writes
    report_sha = awa.ARTIFACT_DIR / f"preflight-{STAMP}-{TOKEN}.json.sha256"
    assert report_sha in fs.writes
    digest = hashlib.sha256(fs.files[PREFLIGHT_JSON]).hexdigest()
    assert fs.files[report_sha] == (
        f"{digest}  preflight-{STAMP}-{TOKEN}.json\n").encode()


def test_source_reports_are_markdown_rendered() -> None:
    rc, _, fs = run(archive_args())
    assert rc == 0
    md = fs.files[REPORT_MD].decode("utf-8")
    assert "M14-43" in md
    assert KEY in md


# ---------------- 报告名防碰撞：CSPRNG 随机后缀 + fail-closed 探测兜底
#
# Round 3 契约：仅靠顺序探测防不了并发——两个进程可在各自探测-写入
# 窗口内同时观察到同一候选名不存在而双双选中、事后互相覆盖证据。
# 每份报告名带 suffix_gen() 生成的 CSPRNG 随机后缀（默认
# secrets.token_hex，32 hex chars = 128 bits），同 command 同秒并发撞名
# 概率约 2**-128；存在性探测循环保留为 fail-closed 兜底（孤儿工件也
# 算碰撞），绝不覆盖既有报告工件。


def test_report_name_contains_command_stamp_and_random_suffix() -> None:
    """[R3-1] 生成名仍含 command 与 stamp，且带随机后缀。"""
    fs = make_fs()
    gen = FakeSuffixGen()
    name = awa._unique_report_name(fs, "preflight", FakeClock(), gen)
    assert name == f"preflight-{STAMP}-{TOKEN}"  # command-stamp-后缀
    assert "preflight" in name and STAMP in name and TOKEN in name
    assert gen.calls == 1
    # 端到端：preflight 落盘工件正是这个名字
    rc, _, fs2 = run(preflight_args(), s3=FakeS3(), fs=fs)
    assert rc == 0
    assert (awa.ARTIFACT_DIR / f"preflight-{STAMP}-{TOKEN}.json") \
        in fs2.files
    assert (awa.ARTIFACT_DIR / f"preflight-{STAMP}-{TOKEN}.md") in fs2.files


def test_repeated_same_clock_archive_never_reuses_or_overwrites() -> None:
    """[R3-2] 同注入时钟重复 archive：不同随机后缀各自成档，零复用零覆盖。

    注入序列后缀生成器模拟两个并发进程各拿不同 CSPRNG 后缀——两次
    报告名仅靠随机后缀即互不相同（无需 ``-2`` 递增），第一份证据字节
    原样保留且证据链仍可 verify 复算通过。
    """
    fs = make_fs()
    s3 = FakeS3()
    rc1 = awa.main(archive_args(), s3=s3, fs=fs, clock=FakeClock(),
                   env=GOOD_ENV,
                   suffix_gen=FakeSuffixGen(["1" * 32]))
    assert rc1 == 0
    first_json_path = awa.ARTIFACT_DIR / f"archive-{STAMP}-{'1' * 32}.json"
    first_json = fs.files[first_json_path]  # 第一份报告字节快照
    # 第二次 archive：对象已存在 → existing-matched 路径（零新 put）
    rc2 = awa.main(archive_args(), s3=s3, fs=fs, clock=FakeClock(),
                   env=GOOD_ENV,
                   suffix_gen=FakeSuffixGen(["2" * 32]))
    assert rc2 == 0
    assert len(s3.put_calls) == 1  # 只有第一次的合法 put
    # 第一份报告工件原样保留（未被覆盖）；第二份走独立随机后缀名
    assert fs.files[first_json_path] == first_json
    second = awa.ARTIFACT_DIR / f"archive-{STAMP}-{'2' * 32}.json"
    sidecar2 = \
        awa.ARTIFACT_DIR / f"archive-{STAMP}-{'2' * 32}.json.sha256"
    assert second in fs.files and sidecar2 in fs.files
    payload2 = json.loads(fs.files[second].decode("utf-8"))
    assert payload2["archive"]["created"] is False
    assert payload2["archive"]["existing_matched"] is True
    # 第二份 sidecar 与第二份报告字节一致
    assert fs.files[sidecar2].decode("utf-8").split()[0] == \
        hashlib.sha256(fs.files[second]).hexdigest()
    # 第一份报告的证据链未被破坏——仍可被 verify 复算通过
    rc_verify = awa.main(
        verify_args(report=f"archive-{STAMP}-{'1' * 32}.json"),
        s3=s3, fs=fs, clock=FakeClock(), env=GOOD_ENV,
        suffix_gen=FakeSuffixGen())
    assert rc_verify == 0


def test_suffix_generation_injectable_without_real_randomness() -> None:
    """[R3-3] 随机后缀生成器可注入——测试不依赖真随机即可断言工件名。

    注入替身决定名字（deterministic ``deadbeef`` * 4）；默认实现只断言
    形态（32 位小写 hex = 128 bits）与多次采样互异，不断言任何特定
    随机值。
    """
    fs = make_fs()
    rc, _, fs2 = run(preflight_args(), s3=FakeS3(), fs=fs,
                     suffix_gen=FakeSuffixGen(["deadbeef" * 4]))
    assert rc == 0
    assert (awa.ARTIFACT_DIR / f"preflight-{STAMP}-{'deadbeef' * 4}.json") \
        in fs2.files
    # 默认生成器：secrets.token_hex(16) → 32 位小写 hex；采样互异
    draws = {awa._random_suffix() for _ in range(8)}
    assert len(draws) == 8
    assert all(re.fullmatch(r"[0-9a-f]{32}", token) for token in draws)


def test_report_name_treats_partial_artifacts_as_collision() -> None:
    """[R3-4] 残留孤儿 .md（json/sidecar 已失）也算碰撞——换名绝不覆盖。"""
    stale_md = awa.ARTIFACT_DIR / f"preflight-{STAMP}-{TOKEN}.md"
    fs = make_fs(extra={stale_md: b"# stale evidence"})
    rc = awa.main(preflight_args(), s3=FakeS3(), fs=fs, clock=FakeClock(),
                  env=GOOD_ENV, suffix_gen=FakeSuffixGen())
    assert rc == 0
    assert fs.files[stale_md] == b"# stale evidence"  # 残留工件未被覆盖
    assert (awa.ARTIFACT_DIR
            / f"preflight-{STAMP}-{TOKEN}-2.json") in fs.files
    assert (awa.ARTIFACT_DIR
            / f"preflight-{STAMP}-{TOKEN}-2.md") in fs.files


def test_report_name_treats_orphan_sidecar_only_artifact_as_collision() -> None:
    """[R3-4 增补] 残留孤儿 sidecar-only 工件（json/md 已失）也算碰撞。

    M14-58 后三命令都写 sidecar，孤儿 ``.json.sha256`` 成为新的残留
    形态——探测循环必须同样换名（``-2``），绝不覆盖任何残留字节。
    """
    stale_sha = awa.ARTIFACT_DIR / f"preflight-{STAMP}-{TOKEN}.json.sha256"
    stale_bytes = f"{'0' * 64}  preflight-{STAMP}-{TOKEN}.json\n".encode()
    fs = make_fs(extra={stale_sha: stale_bytes})
    rc = awa.main(preflight_args(), s3=FakeS3(), fs=fs, clock=FakeClock(),
                  env=GOOD_ENV, suffix_gen=FakeSuffixGen())
    assert rc == 0
    assert fs.files[stale_sha] == stale_bytes  # 孤儿 sidecar 原样保留
    for ext in (".json", ".md", ".json.sha256"):
        assert (awa.ARTIFACT_DIR
                / f"preflight-{STAMP}-{TOKEN}-2{ext}") in fs.files


def test_worst_case_colliding_suffix_probe_increments() -> None:
    """最坏情况（随机后缀完全相同的确定性生成器）：探测兜底递增换名。"""
    fs = make_fs()
    rcs = [awa.main(preflight_args(), s3=FakeS3(), fs=fs, clock=FakeClock(),
                    env=GOOD_ENV,
                    suffix_gen=FakeSuffixGen(["ca11ab1e" * 4]))
           for _ in range(3)]
    assert rcs == [0, 0, 0]
    base = f"preflight-{STAMP}-{'ca11ab1e' * 4}"
    for name in (base, f"{base}-2", f"{base}-3"):
        path = awa.ARTIFACT_DIR / f"{name}.json"
        assert path in fs.files, path  # 三份独立报告，零覆盖


# ---------------------------------------------------------------- RealFS 字节保真


def test_real_fs_write_text_atomic_preserves_unix_newlines(tmp_path) -> None:
    """RealFS 必须以字节落盘：Windows 下不做 \\n→\\r\\n 翻译。"""
    real = awa.RealFS()
    target = tmp_path / "report.json"
    text = '{\n  "a": 1\n}\n'
    real.write_text_atomic(target, text)
    data = target.read_bytes()
    assert data == text.encode("utf-8")
    assert b"\r" not in data


def test_real_fs_archive_roundtrip_sidecar_matches_disk_bytes(
        tmp_path, monkeypatch) -> None:
    """RealFS 端到端：archive 落盘三工件 → verify 用盘上 sidecar 核验通过。

    M14-58：verify 腿自身也必须落盘字节精确的 ``.json.sha256`` sidecar
    （三命令统一三工件）——真实盘上字节与摘要逐一核对。
    """
    artifact_dir = tmp_path / "artifacts"
    monkeypatch.setattr(awa, "ARTIFACT_DIR", artifact_dir)
    anchor = tmp_path / "audit-anchor.jsonl"
    anchor.write_bytes(TWO_ANCHOR_BYTES)
    args_archive = ["archive", "--anchor-file", str(anchor),
                    "--endpoint", ENDPOINT_LOCAL, "--bucket", BUCKET,
                    "--retain-until", RETAIN_UNTIL,
                    "--retention-mode", "COMPLIANCE",
                    "--confirm", awa.CONFIRM_ARCHIVE]
    s3 = FakeS3()
    rc_archive = awa.main(args_archive, s3=s3, fs=awa.RealFS(),
                          clock=FakeClock(), env=GOOD_ENV,
                          suffix_gen=FakeSuffixGen())
    assert rc_archive == 0
    report_json = artifact_dir / f"archive-{STAMP}-{TOKEN}.json"
    report_sha = artifact_dir / f"archive-{STAMP}-{TOKEN}.json.sha256"
    data = report_json.read_bytes()
    assert b"\r" not in data  # Windows 不做换行翻译
    assert data.endswith(b"\n")
    assert report_sha.read_text(encoding="utf-8").split()[
        0] == hashlib.sha256(data).hexdigest()
    args_verify = ["verify", "--anchor-file", str(anchor),
                   "--endpoint", ENDPOINT_LOCAL, "--bucket", BUCKET,
                   "--report", f"archive-{STAMP}-{TOKEN}.json"]
    rc = awa.main(args_verify, s3=s3, fs=awa.RealFS(),
                  clock=FakeClock(), env=GOOD_ENV, suffix_gen=FakeSuffixGen())
    assert rc == 0  # 真实盘上 sidecar 与报告字节一致 → verify 可复算通过
    # M14-58：verify 报告自身的 sidecar 也与盘上字节精确一致
    verify_sha = artifact_dir / f"verify-{STAMP}-{TOKEN}.json.sha256"
    verify_data = (artifact_dir / f"verify-{STAMP}-{TOKEN}.json").read_bytes()
    assert verify_sha.read_text(encoding="utf-8") == (
        f"{hashlib.sha256(verify_data).hexdigest()}"
        f"  verify-{STAMP}-{TOKEN}.json\n")


# ---------------------------------------------------------------- boto3 适配器边界（零网络）


class _StubBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class StubBoto3:
    """boto3 底层客户端替身：记录 kwargs，返回最小合法响应（零网络）。"""

    def __init__(self) -> None:
        self.put_kwargs: dict | None = None
        self.head_kwargs: dict | None = None
        self.get_kwargs: dict | None = None

    def head_bucket(self, **kwargs):
        return {}

    def get_bucket_versioning(self, **kwargs):
        return {"Status": "Enabled"}

    def get_object_lock_configuration(self, **kwargs):
        return {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}

    def head_object(self, **kwargs):
        self.head_kwargs = dict(kwargs)
        return {"VersionId": "ver-1", "ObjectLockMode": "COMPLIANCE",
                "ObjectLockRetainUntilDate": RETAIN_DT,
                "ContentType": awa.CONTENT_TYPE,
                "ContentLength": len(TWO_ANCHOR_BYTES)}

    def get_object(self, **kwargs):
        self.get_kwargs = dict(kwargs)
        return {"Body": _StubBody(TWO_ANCHOR_BYTES)}

    def put_object(self, **kwargs):
        self.put_kwargs = dict(kwargs)
        return {"VersionId": "ver-1"}


def test_boto3_adapter_sends_base64_checksum_sha256() -> None:
    """AWS ChecksumSHA256 线上形态是 base64；hex→base64 只发生在适配器边界。"""
    stub = StubBoto3()
    client = awa.Boto3S3Client(stub)
    returned = client.put_object(
        bucket=BUCKET, key=KEY, body=TWO_ANCHOR_BYTES,
        content_type=awa.CONTENT_TYPE, checksum_sha256=TWO_ANCHOR_SHA,
        object_lock_mode=awa.RETENTION_MODE_COMPLIANCE,
        retain_until=RETAIN_DT)
    assert returned == "ver-1"
    kw = stub.put_kwargs
    expected = base64.b64encode(bytes.fromhex(TWO_ANCHOR_SHA)).decode("ascii")
    assert kw["ChecksumSHA256"] == expected
    assert kw["ChecksumSHA256"] != TWO_ANCHOR_SHA  # base64，不是 hex
    assert kw["Body"] == TWO_ANCHOR_BYTES
    assert kw["Bucket"] == BUCKET and kw["Key"] == KEY
    assert kw["ContentType"] == awa.CONTENT_TYPE
    assert kw["ObjectLockMode"] == "COMPLIANCE"
    assert kw["ObjectLockRetainUntilDate"] == RETAIN_DT


def test_boto3_adapter_targets_recorded_version_on_head_and_get() -> None:
    stub = StubBoto3()
    client = awa.Boto3S3Client(stub)
    head = client.head_object(bucket=BUCKET, key=KEY, version_id="ver-1")
    assert stub.head_kwargs["VersionId"] == "ver-1"
    assert head.version_id == "ver-1"
    assert head.content_type == awa.CONTENT_TYPE
    assert head.size == len(TWO_ANCHOR_BYTES)
    body = client.get_object(bucket=BUCKET, key=KEY, version_id="ver-1")
    assert stub.get_kwargs["VersionId"] == "ver-1"
    assert body == TWO_ANCHOR_BYTES
    # 不带 version（归档存在性探测=最新版本）时省略 VersionId 参数
    client.head_object(bucket=BUCKET, key=KEY)
    assert "VersionId" not in stub.head_kwargs


def test_boto3_adapter_folds_non_hex_checksum_to_s3_error() -> None:
    client = awa.Boto3S3Client(StubBoto3())
    with pytest.raises(awa.S3Error):  # 协议违约（非 hex）折叠，不外泄原始异常
        client.put_object(bucket=BUCKET, key=KEY, body=b"x",
                          content_type=awa.CONTENT_TYPE,
                          checksum_sha256="not-hex",
                          object_lock_mode=awa.RETENTION_MODE_COMPLIANCE,
                          retain_until=RETAIN_DT)


def test_boto3_adapter_maps_if_none_match_create_wire_header() -> None:
    """条件创建哨兵只在 boto3 边界映射为 PutObject IfNoneMatch 线上头。"""
    stub = StubBoto3()
    client = awa.Boto3S3Client(stub)
    client.put_object(
        bucket=BUCKET, key=KEY, body=TWO_ANCHOR_BYTES,
        content_type=awa.CONTENT_TYPE, checksum_sha256=TWO_ANCHOR_SHA,
        object_lock_mode=awa.RETENTION_MODE_COMPLIANCE,
        retain_until=RETAIN_DT, if_none_match=awa.IF_NONE_MATCH_CREATE)
    assert stub.put_kwargs["IfNoneMatch"] == "*"


def test_boto3_adapter_omits_if_none_match_when_not_requested() -> None:
    """未请求条件创建时绝不发送 IfNoneMatch（API 表面保持不变）。"""
    stub = StubBoto3()
    client = awa.Boto3S3Client(stub)
    client.put_object(
        bucket=BUCKET, key=KEY, body=TWO_ANCHOR_BYTES,
        content_type=awa.CONTENT_TYPE, checksum_sha256=TWO_ANCHOR_SHA,
        object_lock_mode=awa.RETENTION_MODE_COMPLIANCE,
        retain_until=RETAIN_DT)
    assert "IfNoneMatch" not in stub.put_kwargs


# ---------------------------------------------------------------- 终防线：意外异常 fail-closed


class ExplodingFS(FakeFS):
    def write_text_atomic(self, path, text: str) -> None:
        raise OSError("disk exploded password=hunter2secret99 "
                      "Bearer tok_abcdef123456")


def test_unexpected_report_write_failure_fails_closed(capsys) -> None:
    """报告写盘意外异常：exit 2、无 traceback、无秘密形态值外泄。"""
    fs = ExplodingFS({ANCHOR_PATH: TWO_ANCHOR_BYTES})
    rc = awa.main(archive_args(), s3=FakeS3(), fs=fs,
                  clock=FakeClock(), env=GOOD_ENV)
    assert rc == 2
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "hunter2secret99" not in combined
    assert "tok_abcdef123456" not in combined
    assert "Traceback" not in combined
