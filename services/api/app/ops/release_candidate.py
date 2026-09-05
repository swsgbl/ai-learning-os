"""M10-17 本地 Release Candidate 包 manifest/校验和/独立校验助手。

infra/build_release_candidate.sh 负责 Docker/compose/git 编排；本模块只做
可独立测试的纯文件操作：tag/VERSION 一致性、输出目录护栏（artifacts/temp
gitignore 边界 + symlink fail-closed + 非空目标拒绝）、release-manifest.json
与 SHA256SUMS 的原子落盘（失败不留看似有效的半成品），以及不加载 Docker
镜像的独立 verify（schema/必填字段/version-tag 一致性/校验和覆盖面/逐档
哈希重算）。

边界：本工具产出的是**本地 Release Candidate**——不是 production
readiness 声明，不授权部署；不推镜像仓库、不打 git tag、不发 GitHub
Release。不读环境变量、不连 DB/网络（AST 级测试守卫），不执行任何命令。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from app.ops.evidence_kit import HEX64_RE
from app.ops.legacy_papers import is_safe_artifact_path

#: tag 形态严格 v{major}.{minor}.{patch}（每位 0 或无前导零整数）。
TAG_RE = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$|^[0-9a-f]{64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

MANIFEST_FILE = "release-manifest.json"
CHECKSUMS_FILE = "SHA256SUMS"
API_REPO = "aios/api"
WEB_REPO = "aios/web"
API_DOCKERFILE = "services/api/Dockerfile"
WEB_DOCKERFILE = "apps/web/Dockerfile"
DEFAULT_COMPOSE_FILE = "infra/docker-compose.yml"
DEFAULT_SMOKE_SCRIPT = "infra/smoke_docker.sh"
DEFAULT_WEB_BUILD_ARG = "http://127.0.0.1:8000"
MANIFEST_SCHEMA_VERSION = 1

#: 固定边界声明（写入 manifest.scope_note / boundary_note 并被 verify 校验）。
SCOPE = "local-release-candidate"
BOUNDARY_NOTE = (
    "LOCAL RELEASE CANDIDATE ONLY. This package was built and verified on a "
    "single local machine from a clean worktree. It is NOT a production "
    "readiness declaration and does not authorize any deployment. Images are "
    "pinned to local Docker tags only: no registry push, no git tag creation, "
    "no GitHub Release publication."
)

#: verify 逐项校验的 manifest 必填顶层字段（str 非空 / int>=0 / dict 语义另行校验）。
REQUIRED_TOP_FIELDS = (
    "schema_version",
    "package_kind",
    "version",
    "tag",
    "git_commit",
    "build_context",
    "web_build_arg",
    "compose",
    "images",
    "archives",
    "checksums_file",
    "smoke",
    "scope",
    "scope_note",
    "boundary_note",
)
REQUIRED_IMAGE_FIELDS = ("repository", "tag", "image_id", "local_only")
REQUIRED_ARCHIVE_FIELDS = ("component", "filename", "sha256", "size_bytes")
REQUIRED_COMPOSE_FIELDS = ("file", "sha256")


class ReleaseCandidateError(Exception):
    """输入/路径/IO 问题（CLI 退出码 2：不是包结论，是输入不可用）。"""


# --- tag / VERSION 一致性 -------------------------------------------------------


def parse_tag(tag: str) -> str:
    """校验 tag 严格为 vX.Y.Z；返回裸版本 X.Y.Z（与 VERSION 文件比对用）。"""
    if not isinstance(tag, str) or not TAG_RE.match(tag):
        raise ReleaseCandidateError(
            "tag 必须形如 vX.Y.Z（如 v0.1.0），收到: " + repr(tag)
        )
    return tag[1:]


def read_version_file(path: str | Path) -> str:
    """读 VERSION 文件并去除全部空白；空内容拒绝（版本真相源不得为空）。"""
    file_path = Path(path)
    if file_path.is_symlink():
        raise ReleaseCandidateError("VERSION 文件是符号链接，拒绝读取: " + str(path))
    if not file_path.is_file():
        raise ReleaseCandidateError("VERSION 文件不存在或不是常规文件: " + str(path))
    text = "".join(file_path.read_text(encoding="utf-8").split())
    if not text:
        raise ReleaseCandidateError("VERSION 文件内容为空: " + str(path))
    return text


def check_tag_version_consistency(tag: str, version_file: str | Path) -> str:
    """tag 剥掉 v 前缀后必须与 VERSION 文件逐字相等；返回裸版本。"""
    bare = parse_tag(tag)
    declared = read_version_file(version_file)
    if bare != declared:
        raise ReleaseCandidateError(
            f"tag {tag} 与 VERSION 文件不一致：VERSION={declared!r}，"
            f"tag 裸版本={bare!r}（必须逐字相等）"
        )
    return bare


# --- 输出目录护栏 --------------------------------------------------------------


def _reject_symlink_ancestry(path: Path) -> None:
    """路径本身与任何已存在祖先组件是 symlink 都拒绝（不猜链接目标）。"""
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ReleaseCandidateError(
                "路径组件是符号链接，拒绝写入: " + str(candidate)
            )


def resolve_package_dir(raw: str | Path, *, for_writing: bool) -> Path:
    """RC 包目录护栏。

    - 任何已存在路径组件是 symlink => 拒绝（含 .. 越界经 resolve 归一后
      仍须落在护栏内）；
    - 复用 is_safe_artifact_path（直接父目录名是 artifacts/temp，或被
      git check-ignore 判定忽略；仓库外路径一律拒绝）；
    - 目录不能是 artifacts/ 或 temp/ 本身；
    - for_writing：已存在必须是目录（普通文件/symlink 拒绝）；目录内只允许
      存在调用方声明的两个镜像归档（write_release_package 校验，任何其他
      既有条目都拒绝——绝不混写）。for_writing=False（verify）：必须已存在
      且为真目录。
    """
    target = Path(raw)
    _reject_symlink_ancestry(target)
    resolved = target.resolve()
    if resolved.name.lower() in ("artifacts", "temp"):
        raise ReleaseCandidateError(
            "包目录不能是 artifacts/ 或 temp/ 本身（必须是其中独占的新目录）: "
            + str(raw)
        )
    if not is_safe_artifact_path(resolved):
        raise ReleaseCandidateError(
            "拒绝使用 " + str(raw) + "：Release Candidate 包只能落在 gitignore 的 "
            "artifacts/ 或 temp/ 目录内（直接父目录名为 artifacts/temp，或被 "
            "git check-ignore 判定忽略；仓库外任意路径一律拒绝）"
        )
    if for_writing:
        if os.path.lexists(resolved) and (
            resolved.is_symlink() or not resolved.is_dir()
        ):
            raise ReleaseCandidateError(
                "输出目录已存在且不是目录: " + str(resolved)
            )
    else:
        if not os.path.lexists(resolved):
            raise ReleaseCandidateError("包目录不存在: " + str(resolved))
        if resolved.is_symlink() or not resolved.is_dir():
            raise ReleaseCandidateError("包目录不是真目录: " + str(resolved))
    return resolved


def _check_regular_file(path: Path, what: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ReleaseCandidateError(what + " 不是常规文件（拒绝）: " + str(path))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- 原子写 --------------------------------------------------------------------


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """同目录临时文件 + os.replace 原子落盘；失败清理临时文件、不写穿 symlink。"""
    _check_regular_file_guard(path)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix="." + path.name + ".tmp-"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _check_regular_file_guard(path: Path) -> None:
    if path.is_symlink():
        raise ReleaseCandidateError("目标文件是符号链接，拒绝写入: " + str(path))


# --- manifest 构建与包写入 ------------------------------------------------------


def _validate_archive_name(name: str, *, reserved: set[str]) -> str:
    if not isinstance(name, str) or not name:
        raise ReleaseCandidateError("归档文件名不能为空")
    if "/" in name or "\\" in name or name in (".", ".."):
        raise ReleaseCandidateError(
            "归档文件名只能是输出目录内的纯文件名（无路径分隔符）: " + repr(name)
        )
    if name in reserved:
        raise ReleaseCandidateError("归档文件名与包保留文件名冲突: " + repr(name))
    return name


def _compose_file_label(compose_path: Path) -> str:
    """compose 文件以 POSIX 相对仓库根路径记录（跨机可比），必要时用原名。"""
    normalized = compose_path.as_posix()
    marker = "/infra/docker-compose.yml"
    if normalized.endswith(marker):
        return "infra/docker-compose.yml"
    return compose_path.name


def build_manifest(
    *,
    tag: str,
    version_file: str | Path,
    git_commit: str,
    compose_file: str | Path,
    api_image_id: str,
    web_image_id: str,
    build_context: str = ".",
    web_build_arg: str = DEFAULT_WEB_BUILD_ARG,
    smoke_script: str = DEFAULT_SMOKE_SCRIPT,
    archive_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """组装 manifest dict（纯函数：归档哈希/大小由调用方经 archive_records 传入）。"""
    if archive_records is None:
        archive_records = []
    version = check_tag_version_consistency(tag, version_file)
    if not GIT_COMMIT_RE.match(git_commit):
        raise ReleaseCandidateError(
            "git commit 必须是 40/64 位十六进制 SHA，收到: " + repr(git_commit)
        )
    compose_path = Path(compose_file)
    _check_regular_file(compose_path, "compose 文件")
    for repo, image_id in ((API_REPO, api_image_id), (WEB_REPO, web_image_id)):
        if not IMAGE_ID_RE.match(image_id):
            raise ReleaseCandidateError(
                f"{repo} 镜像 ID 必须形如 sha256:<64hex>，收到: " + repr(image_id)
            )
    if not isinstance(web_build_arg, str) or not web_build_arg:
        raise ReleaseCandidateError("web build arg（NEXT_PUBLIC_API_BASE_URL）为空")
    if not isinstance(build_context, str) or not build_context:
        raise ReleaseCandidateError("build context 为空")
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "package_kind": "release-candidate",
        "version": version,
        "tag": tag,
        "git_commit": git_commit,
        "build_context": {
            "root": build_context,
            "api": {"dockerfile": API_DOCKERFILE, "context": build_context},
            "web": {"dockerfile": WEB_DOCKERFILE, "context": build_context},
        },
        "web_build_arg": {"NEXT_PUBLIC_API_BASE_URL": web_build_arg},
        "compose": {
            "file": _compose_file_label(compose_path),
            "sha256": sha256_file(compose_path),
        },
        "images": {
            "api": {
                "repository": API_REPO,
                "tag": tag,
                "image_id": api_image_id,
                "local_only": True,
            },
            "web": {
                "repository": WEB_REPO,
                "tag": tag,
                "image_id": web_image_id,
                "local_only": True,
            },
        },
        "archives": archive_records,
        "checksums_file": CHECKSUMS_FILE,
        "smoke": {"script": smoke_script, "result": "passed"},
        "scope": SCOPE,
        "scope_note": "local build + local verify only; not production readiness",
        "boundary_note": BOUNDARY_NOTE,
    }


def render_checksums(entries: dict[str, str]) -> bytes:
    """sha256sum 兼容行（64hex + 两空格 + 文件名），按文件名排序（确定性）。"""
    lines = []
    for name in sorted(entries):
        digest = entries[name]
        if not HEX64_RE.match(digest):
            raise ReleaseCandidateError(
                "校验和必须是 64 位小写十六进制: " + repr(digest)
            )
        lines.append(f"{digest}  {name}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def write_release_package(
    output_dir: str | Path,
    *,
    tag: str,
    version_file: str | Path,
    git_commit: str,
    compose_file: str | Path,
    api_image_id: str,
    web_image_id: str,
    api_archive: str,
    web_archive: str,
    build_context: str = ".",
    web_build_arg: str = DEFAULT_WEB_BUILD_ARG,
    smoke_script: str = DEFAULT_SMOKE_SCRIPT,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """在护栏内空目录写入 release-manifest.json + SHA256SUMS（原子、失败清理）。

    调用方（build 脚本）需先把两个镜像归档保存到 output_dir；本函数校验
    归档存在且为常规文件后计算哈希、组装 manifest、先写 manifest 再写覆盖
    manifest 自身字节的 SHA256SUMS。任何失败都会清理本次已落盘的最终文件
    与临时文件——绝不留下看似有效的半成品 manifest。
    """
    if generated_at is not None and (
        not isinstance(generated_at, str) or not generated_at.strip()
    ):
        raise ReleaseCandidateError("generated_at 必须是非空字符串")
    reserved = {MANIFEST_FILE, CHECKSUMS_FILE}
    api_name = _validate_archive_name(api_archive, reserved=reserved)
    web_name = _validate_archive_name(web_archive, reserved=reserved)
    if api_name == web_name:
        raise ReleaseCandidateError("api 与 web 归档文件名必须不同")

    out_dir = resolve_package_dir(output_dir, for_writing=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    if out_dir.is_dir():
        allowed = {api_name, web_name}
        strays = sorted(
            entry.name for entry in out_dir.iterdir() if entry.name not in allowed
        )
        if strays:
            raise ReleaseCandidateError(
                "输出目录存在预期镜像归档之外的条目（拒绝混写既有文件）: "
                + repr(strays)
            )
    archive_records: list[dict[str, Any]] = []
    for component, name in (("api", api_name), ("web", web_name)):
        path = out_dir / name
        _check_regular_file(path, f"{component} 镜像归档")
        archive_records.append(
            {
                "component": component,
                "filename": name,
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )

    manifest = build_manifest(
        tag=tag,
        version_file=version_file,
        git_commit=git_commit,
        compose_file=compose_file,
        api_image_id=api_image_id,
        web_image_id=web_image_id,
        build_context=build_context,
        web_build_arg=web_build_arg,
        smoke_script=smoke_script,
        archive_records=archive_records,
    )
    if generated_at is not None:
        manifest["generated_at"] = generated_at

    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    checksums = render_checksums(
        {rec["filename"]: rec["sha256"] for rec in archive_records}
        | {MANIFEST_FILE: hashlib.sha256(manifest_bytes).hexdigest()}
    )

    written: list[Path] = []
    try:
        atomic_write_bytes(out_dir / MANIFEST_FILE, manifest_bytes)
        written.append(out_dir / MANIFEST_FILE)
        atomic_write_bytes(out_dir / CHECKSUMS_FILE, checksums)
        written.append(out_dir / CHECKSUMS_FILE)
    except BaseException:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return {
        "tool": "release-candidate",
        "action": "manifest",
        "package_dir": str(out_dir),
        "manifest_file": MANIFEST_FILE,
        "checksums_file": CHECKSUMS_FILE,
        "archives": [rec["filename"] for rec in archive_records],
        "tag": tag,
        "version": manifest["version"],
        "git_commit": git_commit,
        "scope": SCOPE,
        "boundary_note": BOUNDARY_NOTE,
    }


# --- 独立 verify（不加载 Docker 镜像）-------------------------------------------


def _fail(report: dict[str, Any], checks: list[dict[str, Any]],
          name: str, detail: str) -> None:
    checks.append({"check": name, "result": "fail", "detail": detail})
    report["problems"].append(f"{name}: {detail}")


def _pass(checks: list[dict[str, Any]], name: str, detail: str) -> None:
    checks.append({"check": name, "result": "pass", "detail": detail})


def _summary(checks: list[dict[str, Any]]) -> dict[str, int]:
    passed = sum(1 for c in checks if c["result"] == "pass")
    return {"total": len(checks), "passed": passed,
            "failed": len(checks) - passed}


def verify_release_package(
    package_dir: str | Path,
    *,
    version_file: str | Path | None = None,
) -> dict[str, Any]:
    """独立校验 RC 包：schema/必填字段/version-tag 一致性/校验和覆盖面/逐档哈希。

    只读本地文件并重算 SHA-256——绝不调用 docker load/inspect（不加载镜像、
    不起容器）。返回 report（ok/problems/checks）；输入不可用抛
    ReleaseCandidateError（CLI exit 2），校验失败 ok=False（CLI exit 1）。
    """
    package = resolve_package_dir(package_dir, for_writing=False)
    report: dict[str, Any] = {
        "tool": "release-candidate-verify",
        "package_dir": str(package),
        "ok": False,
        "problems": [],
        "checks": [],
    }
    checks: list[dict[str, Any]] = report["checks"]
    entries = {entry for entry in package.iterdir()}
    for entry in sorted(entries):
        if entry.is_symlink() or not entry.is_file():
            _fail(report, checks, "package_entries",
                  f"包内条目不是常规文件: {entry.name}")

    manifest_path = package / MANIFEST_FILE
    if not manifest_path.is_file() or manifest_path.is_symlink():
        _fail(report, checks, "manifest_present", f"缺 {MANIFEST_FILE}")
        report["checks_summary"] = _summary(checks)
        return report
    try:
        manifest = json.loads(manifest_path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as cause:
        _fail(report, checks, "manifest_json",
              f"manifest 不是合法 UTF-8 JSON: {cause}")
        report["checks_summary"] = _summary(checks)
        return report
    if not isinstance(manifest, dict):
        _fail(report, checks, "manifest_json", "manifest 不是 JSON 对象")
        report["checks_summary"] = _summary(checks)
        return report

    # --- schema：必填字段与类型 -------------------------------------------------
    for field in REQUIRED_TOP_FIELDS:
        if field not in manifest:
            _fail(report, checks, "manifest_schema", f"缺必填字段 {field}")
    for field in ("version", "tag", "git_commit", "scope_note",
                  "boundary_note", "checksums_file", "package_kind", "scope"):
        value = manifest.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            _fail(report, checks, "manifest_schema",
                  f"字段 {field} 必须是非空字符串")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        _fail(report, checks, "manifest_schema",
              f"schema_version 必须是 {MANIFEST_SCHEMA_VERSION}，"
              f"收到 {manifest.get('schema_version')!r}")
    if manifest.get("package_kind") not in (None, "release-candidate"):
        _fail(report, checks, "manifest_schema",
              "package_kind 必须是 release-candidate，收到 "
              + repr(manifest.get("package_kind")))
    web_build_arg = manifest.get("web_build_arg")
    if web_build_arg is not None:
        if not isinstance(web_build_arg, dict) or set(web_build_arg) != {
            "NEXT_PUBLIC_API_BASE_URL"
        }:
            _fail(report, checks, "manifest_schema",
                  "web_build_arg 必须只含 NEXT_PUBLIC_API_BASE_URL，收到 "
                  + repr(sorted(web_build_arg) if isinstance(
                      web_build_arg, dict) else web_build_arg))
        elif not isinstance(
            web_build_arg["NEXT_PUBLIC_API_BASE_URL"], str
        ) or not web_build_arg["NEXT_PUBLIC_API_BASE_URL"].strip():
            _fail(report, checks, "manifest_schema",
                  "web_build_arg.NEXT_PUBLIC_API_BASE_URL 必须是非空字符串")

    images = manifest.get("images")
    archive_records = manifest.get("archives")
    compose_info = manifest.get("compose")
    if not isinstance(images, dict) or set(images) < {"api", "web"}:
        _fail(report, checks, "manifest_schema", "images 必须含 api 与 web")
        images = images if isinstance(images, dict) else {}
    for component in ("api", "web"):
        image = images.get(component)
        if image is None:
            continue
        if not isinstance(image, dict):
            _fail(report, checks, "manifest_schema",
                  f"images.{component} 不是对象")
            continue
        for field in REQUIRED_IMAGE_FIELDS:
            if field not in image:
                _fail(report, checks, "manifest_schema",
                      f"images.{component} 缺 {field}")
        if not IMAGE_ID_RE.match(str(image.get("image_id", ""))):
            _fail(report, checks, "manifest_schema",
                  f"images.{component}.image_id 必须形如 sha256:<64hex>，"
                  f"收到 {image.get('image_id')!r}")
    if not isinstance(archive_records, list) or len(archive_records) != 2:
        _fail(report, checks, "manifest_schema",
              "archives 必须是恰好 2 条（api + web）")
        archive_records = archive_records if isinstance(
            archive_records, list
        ) else []
    archive_names: list[str] = []
    for record in archive_records:
        if not isinstance(record, dict):
            _fail(report, checks, "manifest_schema", "archives 条目不是对象")
            continue
        for field in REQUIRED_ARCHIVE_FIELDS:
            if field not in record:
                _fail(report, checks, "manifest_schema",
                      f"archives 条目缺 {field}")
        name = record.get("filename")
        size = record.get("size_bytes")
        sha = record.get("sha256")
        if isinstance(name, str):
            archive_names.append(name)
        if isinstance(sha, str) and not HEX64_RE.match(sha):
            _fail(report, checks, "manifest_schema",
                  f"archives {name!r} sha256 不是 64 位小写十六进制")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            _fail(report, checks, "manifest_schema",
                  f"archives {name!r} size_bytes 必须是非负整数")
    if len(archive_names) != 2 or len(set(archive_names)) != 2:
        _fail(report, checks, "manifest_schema",
              "archives 文件名必须互不相同且恰好覆盖 api 与 web")
    if not isinstance(compose_info, dict):
        _fail(report, checks, "manifest_schema", "compose 不是对象")
    else:
        for field in REQUIRED_COMPOSE_FIELDS:
            if field not in compose_info:
                _fail(report, checks, "manifest_schema", f"compose 缺 {field}")
        if isinstance(compose_info.get("sha256"), str) and not HEX64_RE.match(
            compose_info["sha256"]
        ):
            _fail(report, checks, "manifest_schema",
                  "compose.sha256 不是 64 位小写十六进制")
    if manifest.get("checksums_file") not in (None, CHECKSUMS_FILE):
        _fail(report, checks, "manifest_schema",
              "checksums_file 必须是 " + CHECKSUMS_FILE)

    # --- version / tag 一致性 ---------------------------------------------------
    tag = manifest.get("tag")
    version = manifest.get("version")
    if isinstance(tag, str) and isinstance(version, str):
        if not TAG_RE.match(tag):
            _fail(report, checks, "version_tag_consistency",
                  f"tag 不符合 vX.Y.Z: {tag!r}")
        elif tag[1:] != version:
            _fail(report, checks, "version_tag_consistency",
                  f"tag {tag!r} 剥离 v 后与 version {version!r} 不等")
        else:
            for component in ("api", "web"):
                image = images.get(component)
                if isinstance(image, dict) and image.get("tag") != tag:
                    _fail(report, checks, "version_tag_consistency",
                          f"images.{component}.tag 必须等于 {tag!r}，"
                          f"收到 {image.get('tag')!r}")
            _pass(checks, "version_tag_consistency",
                  f"tag={tag} version={version} 镜像 tag 同步")
    if manifest.get("scope") not in (None, SCOPE):
        _fail(report, checks, "scope_boundary",
              f"scope 必须是 {SCOPE!r}，收到 {manifest.get('scope')!r}")
    boundary = manifest.get("boundary_note")
    if isinstance(boundary, str):
        lowered = boundary.lower()
        if ("local" not in lowered or "not" not in lowered
                or "production readiness" not in lowered):
            _fail(report, checks, "scope_boundary",
                  "boundary_note 必须显式声明本地 RC 且非 production readiness 声明")
    if version_file is not None and isinstance(version, str):
        try:
            declared = read_version_file(version_file)
        except ReleaseCandidateError as cause:
            _fail(report, checks, "version_file_consistency", str(cause))
        else:
            if declared != version:
                _fail(report, checks, "version_file_consistency",
                      f"manifest version {version!r} 与 VERSION 文件 "
                      f"{declared!r} 不一致")
            else:
                _pass(checks, "version_file_consistency",
                      f"VERSION 文件与 manifest version 一致（{declared}）")

    # --- SHA256SUMS 覆盖面与逐档哈希（不加载镜像，只重算文件哈希）--------------
    sums_path = package / CHECKSUMS_FILE
    expected_files = set(archive_names) | {MANIFEST_FILE}
    sums: dict[str, str] = {}
    if not sums_path.is_file() or sums_path.is_symlink():
        _fail(report, checks, "checksums_present", f"缺 {CHECKSUMS_FILE}")
    else:
        try:
            lines = sums_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as cause:
            _fail(report, checks, "checksums_parse", str(cause))
            lines = []
        for line in lines:
            match = re.match(r"^([0-9a-f]{64})  ([A-Za-z0-9._+-]+)$", line)
            if not match:
                _fail(report, checks, "checksums_parse",
                      f"SHA256SUMS 行不合形态: {line!r}")
                continue
            digest, name = match.groups()
            if name in sums:
                _fail(report, checks, "checksums_parse",
                      f"SHA256SUMS 重复条目: {name}")
            sums[name] = digest
        missing = expected_files - set(sums)
        extra = set(sums) - expected_files
        if missing or extra:
            _fail(report, checks, "checksums_coverage",
                  f"SHA256SUMS 覆盖面不符（缺 {sorted(missing)}；"
                  f"多 {sorted(extra)}）")
        else:
            _pass(checks, "checksums_coverage",
                  f"恰覆盖 {sorted(expected_files)}")
        on_disk = {entry.name for entry in entries if entry.is_file()}
        unrecognized = on_disk - expected_files - {CHECKSUMS_FILE}
        if unrecognized:
            _fail(report, checks, "checksums_coverage",
                  f"包内存在未被校验和覆盖的文件: {sorted(unrecognized)}")
        for name in sorted(expected_files):
            path = package / name
            record = next(
                (r for r in archive_records
                 if isinstance(r, dict) and r.get("filename") == name),
                None,
            )
            if not path.is_file() or path.is_symlink():
                _fail(report, checks, "archive_hash", f"文件缺失: {name}")
                continue
            actual = sha256_file(path)
            if name in sums and sums[name] != actual:
                _fail(report, checks, "archive_hash",
                      f"{name} 实际哈希与 SHA256SUMS 不符（篡改或损坏）")
            if record is not None:
                declared_sha = record.get("sha256")
                if isinstance(declared_sha, str) and declared_sha != actual:
                    _fail(report, checks, "archive_hash",
                          f"{name} 实际哈希与 manifest 声明不符（篡改或损坏）")
                declared_size = record.get("size_bytes")
                if isinstance(declared_size, int) and declared_size != (
                    path.stat().st_size
                ):
                    _fail(report, checks, "archive_hash",
                          f"{name} 实际大小与 manifest size_bytes 不符")
            if name in sums and sums[name] == actual and (
                record is None or record.get("sha256") == actual
            ):
                _pass(checks, "archive_hash",
                      f"{name} sha256 重算一致（未加载镜像）")

    report["ok"] = not report["problems"]
    report["checks_summary"] = _summary(checks)
    return report
