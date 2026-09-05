"""M10-17 本地 Release Candidate 打包：manifest 助手 + build 脚本 + 工作流契约。

覆盖矩阵：
1. tag/VERSION 一致性：严格 vX.Y.Z 形态、VERSION 逐字一致、畸形输入拒绝；
2. 路径安全：artifacts/temp gitignore 护栏外拒绝、`..` 越界拒绝、
   artifacts/temp 本身拒绝、已存在普通文件拒绝、非空目录拒绝（脚本层
   任何非空都拒绝；模块层只放行恰好两个预期镜像归档）、symlink 目标与
   symlink 祖先组件拒绝（无 symlink 权限环境 skip）；
3. manifest/SHA256SUMS：必填字段齐备（version/tag/git commit/compose 哈希/
   image IDs/归档文件名+哈希+大小/build context/web build arg/scope 边界
   声明）、校验和恰覆盖两归档+manifest 自身、原子写失败清理（不留看似
   有效的半成品、无 .tmp 残留）；
4. verify 独立校验（不加载 Docker 镜像）：干净包 ok；篡改归档/manifest/
   SHA256SUMS、覆盖面缺/多、非法行、重复条目、多余文件、缺文件全部失败；
   version-tag-VERSION 三方一致性；schema 缺字段/坏类型 fail；「自洽篡改」
   （改 manifest 后重算 SHA256SUMS 绕过哈希门）被 schema/一致性门拦下；
5. 零敏感：产物与 CLI 输出无密码/token/key 字样、无凭据 URL 形态；
   AST 级零 os.environ/零 subprocess/零网络/零 DB 引用；
6. CLI 注册与行为面：manifest/verify 退出码 0/1/2 矩阵、边界声明输出；
7. build 脚本契约：bash -n 语法、tag 正则、VERSION 比对、干净 worktree、
   AIOS_IMAGE_TAG + --no-build、smoke 默认 infra/smoke_docker.sh、
   down --remove-orphans 且绝无 -v、docker save 独立归档、manifest/verify
   子命令、symlink 组件检查、代码行零 git tag/git push/docker push/
   docker login/gh release；
8. build 脚本行为面（stub git/docker/python + 真实 manifest 助手）：
   快乐路径全序列、脏 worktree 早退、tag 失配早退、非空目录拒绝且不动
   既有文件、护栏外拒绝、smoke 失败清理 compose 且不留半成品包；
9. 工作流契约：仅 workflow_dispatch（无 push/pull_request/schedule）、
   最小权限、Linux runner、upload-artifact 上传且零发布动词；
10. 真实 Docker 构建冒烟只在 AIOS_RELEASE_SMOKE=1 时执行（默认 skip）。

全部测试只用临时目录与本地文件，不连接任何数据库、不发起任何网络请求、
不调用真实 Docker。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ops import cli as cli_module
from app.ops.release_candidate import (
    BOUNDARY_NOTE,
    CHECKSUMS_FILE,
    MANIFEST_FILE,
    SCOPE,
    ReleaseCandidateError,
    check_tag_version_consistency,
    parse_tag,
    read_version_file,
    render_checksums,
    resolve_package_dir,
    verify_release_package,
    write_release_package,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = REPO_ROOT / "infra" / "build_release_candidate.sh"
BUILD_SCRIPT_RELATIVE = "infra/build_release_candidate.sh"
WORKFLOW_FILE = REPO_ROOT / ".github" / "workflows" / "release-candidate.yml"
BASH = shutil.which("bash")

GIT_SHA = "a" * 40
API_ID = "sha256:" + "b" * 64
WEB_ID = "sha256:" + "c" * 64
PASSWORD_MARKER = "PROD-PW-77e1"
API_KEY_MARKER = "sk-PROD-KEY-77e2"
_CREDENTIAL_URL_RE = re.compile(r"://\S+:\S+@")

pytestmark_bash = pytest.mark.skipif(BASH is None, reason="bash 不可用")


# --- 测试脚手架 ---------------------------------------------------------------


def _version_file(tmp_path: Path, text: str = "0.1.0") -> Path:
    path = tmp_path / "VERSION"
    path.write_text(text + "\n", encoding="utf-8")
    return path


def _compose_file(tmp_path: Path, text: str = "services: {version: rc}") -> Path:
    path = tmp_path / "infra" / "docker-compose.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _seed_archives(package_dir: Path, tag: str = "v0.1.0",
                   api_bytes: bytes = b"API-IMAGE-BYTES",
                   web_bytes: bytes = b"WEB-IMAGE-BYTES") -> tuple[str, str]:
    api_name = f"aios-api-{tag}.tar"
    web_name = f"aios-web-{tag}.tar"
    (package_dir / api_name).write_bytes(api_bytes)
    (package_dir / web_name).write_bytes(web_bytes)
    return api_name, web_name


def _build_package(tmp_path: Path, *, tag: str = "v0.1.0",
                   version_text: str = "0.1.0",
                   with_archives: bool = True) -> Path:
    """在护栏内（tmp_path/artifacts/rc-<tag>）构造一个完整 RC 包。"""
    package_dir = tmp_path / "artifacts" / f"rc-{tag}"
    package_dir.mkdir(parents=True)
    if with_archives:
        _seed_archives(package_dir, tag)
    write_release_package(
        package_dir,
        tag=tag,
        version_file=_version_file(tmp_path, version_text),
        git_commit=GIT_SHA,
        compose_file=_compose_file(tmp_path),
        api_image_id=API_ID,
        web_image_id=WEB_ID,
        api_archive=f"aios-api-{tag}.tar",
        web_archive=f"aios-web-{tag}.tar",
        generated_at="2026-09-05T00:00:00Z",
    )
    return package_dir


def _rewrite_manifest_and_sums(package_dir: Path, mutate) -> None:
    """改 manifest 后重算 SHA256SUMS——模拟「自洽篡改」（绕过字节哈希门，
    只有 schema/一致性门能拦）。校验和条目按磁盘上的归档重算，不依赖
    manifest 自身的 archives 声明（mutate 可能已改/删它）。"""
    manifest = json.loads(
        (package_dir / MANIFEST_FILE).read_text(encoding="utf-8")
    )
    mutate(manifest)
    payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    (package_dir / MANIFEST_FILE).write_bytes(payload)
    entries = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(package_dir.iterdir())
        if p.is_file() and p.name.endswith(".tar")
    }
    entries[MANIFEST_FILE] = hashlib.sha256(payload).hexdigest()
    (package_dir / CHECKSUMS_FILE).write_bytes(render_checksums(entries))


def _load_manifest(package_dir: Path) -> dict:
    return json.loads(
        (package_dir / MANIFEST_FILE).read_text(encoding="utf-8")
    )


# --- 1. tag / VERSION 一致性 ----------------------------------------------------


@pytest.mark.parametrize("tag", ["v0.1.0", "v0.0.0", "v10.20.30", "v1.0.0"])
def test_parse_tag_accepts_strict_forms(tag: str) -> None:
    """严格 vX.Y.Z（每位 0 或无前导零）通过，返回裸版本。"""
    assert parse_tag(tag) == tag[1:]


@pytest.mark.parametrize(
    "tag",
    [
        "0.1.0", "v0.1", "v0.1.0.0", "v01.2.3", "v0.1.0-rc1", "v0.1.0+meta",
        "V0.1.0", "release-0.1.0", "", " v0.1.0", "vx.y.z", "v0.1.x",
    ],
)
def test_parse_tag_rejects_malformed(tag: str) -> None:
    with pytest.raises(ReleaseCandidateError):
        parse_tag(tag)


def test_read_version_file_strips_all_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "VERSION"
    path.write_text(" 0.1.0 \r\n", encoding="utf-8")
    assert read_version_file(path) == "0.1.0"


@pytest.mark.parametrize("content", ["", "   \n", "\t"])
def test_read_version_file_rejects_empty(tmp_path: Path, content: str) -> None:
    path = tmp_path / "VERSION"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ReleaseCandidateError):
        read_version_file(path)


def test_read_version_file_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(ReleaseCandidateError):
        read_version_file(tmp_path / "missing")


def test_check_tag_version_consistency_exact_match(tmp_path: Path) -> None:
    assert check_tag_version_consistency(
        "v0.2.13", _version_file(tmp_path, "0.2.13")
    ) == "0.2.13"


@pytest.mark.parametrize("tag,version", [("v0.1.0", "0.1.1"), ("v1.0.0", "0.1.0")])
def test_check_tag_version_consistency_mismatch(
    tmp_path: Path, tag: str, version: str
) -> None:
    with pytest.raises(ReleaseCandidateError, match="不一致"):
        check_tag_version_consistency(tag, _version_file(tmp_path, version))


# --- 2. 路径安全护栏 ------------------------------------------------------------


def test_resolve_package_dir_accepts_new_dir_under_artifacts_or_temp(
    tmp_path: Path,
) -> None:
    """护栏内新目录（artifacts/ 或 temp/ 直接子目录）放行并归一。"""
    for parent in ("artifacts", "temp"):
        target = tmp_path / parent / "rc-v0.1.0"
        assert resolve_package_dir(target, for_writing=True) == target.resolve()


def test_resolve_package_dir_accepts_existing_empty_dir(tmp_path: Path) -> None:
    target = tmp_path / "artifacts" / "rc"
    target.mkdir(parents=True)
    assert resolve_package_dir(target, for_writing=True) == target.resolve()


@pytest.mark.parametrize(
    "raw",
    [
        "pack", "nested/deeper/pack", "artifacts2/pack", "rc-v0.1.0",
    ],
)
def test_resolve_package_dir_rejects_outside_guardrail(
    tmp_path: Path, raw: str
) -> None:
    with pytest.raises(ReleaseCandidateError):
        resolve_package_dir(tmp_path / raw, for_writing=True)
    assert not (tmp_path / raw).exists(), "拒绝时不得留下任何目录"


def test_resolve_package_dir_rejects_dotdot_escape(tmp_path: Path) -> None:
    sneaky = tmp_path / "artifacts" / ".." / "escape" / "pack"
    with pytest.raises(ReleaseCandidateError):
        resolve_package_dir(sneaky, for_writing=True)
    assert not (tmp_path / "escape").exists()


def test_resolve_package_dir_rejects_artifacts_or_temp_itself(
    tmp_path: Path,
) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "temp").mkdir()
    with pytest.raises(ReleaseCandidateError):
        resolve_package_dir(tmp_path / "artifacts", for_writing=True)
    with pytest.raises(ReleaseCandidateError):
        resolve_package_dir(tmp_path / "temp", for_writing=True)


def test_resolve_package_dir_rejects_existing_file(tmp_path: Path) -> None:
    occupied = tmp_path / "artifacts" / "rc"
    occupied.parent.mkdir(parents=True)
    occupied.write_text("occupied", encoding="utf-8")
    with pytest.raises(ReleaseCandidateError):
        resolve_package_dir(occupied, for_writing=True)
    assert occupied.read_text(encoding="utf-8") == "occupied"


def test_resolve_package_dir_symlink_target_and_ancestors(
    tmp_path: Path,
) -> None:
    """symlink 目标与 symlink 祖先组件都拒绝（无 symlink 权限环境 skip）。"""
    real = tmp_path / "artifacts" / "real"
    real.mkdir(parents=True)
    try:
        link_target = tmp_path / "artifacts" / "link-target"
        os.symlink(real, link_target, target_is_directory=True)
        outside_link = tmp_path / "outside-link"
        os.symlink(real, outside_link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("此环境无法创建 symlink（Windows 需开发者模式/特权）")
    with pytest.raises(ReleaseCandidateError):
        resolve_package_dir(link_target, for_writing=True)
    with pytest.raises(ReleaseCandidateError):
        resolve_package_dir(outside_link / "pack", for_writing=True)


def test_resolve_package_dir_verify_mode_requires_existing_dir(
    tmp_path: Path,
) -> None:
    with pytest.raises(ReleaseCandidateError):
        resolve_package_dir(tmp_path / "artifacts" / "missing", for_writing=False)
    occupied = tmp_path / "artifacts" / "afile"
    occupied.parent.mkdir(parents=True)
    occupied.write_text("x", encoding="utf-8")
    with pytest.raises(ReleaseCandidateError):
        resolve_package_dir(occupied, for_writing=False)


# --- 3. manifest / SHA256SUMS 写入 ----------------------------------------------


def test_write_package_manifest_required_fields(tmp_path: Path) -> None:
    """manifest 至少含任务要求的全部字段；compose 哈希独立重算一致。"""
    package_dir = _build_package(tmp_path)
    manifest = _load_manifest(package_dir)
    compose_path = _compose_file(tmp_path)
    assert manifest["version"] == "0.1.0"
    assert manifest["tag"] == "v0.1.0"
    assert manifest["git_commit"] == GIT_SHA
    assert manifest["compose"] == {
        "file": "infra/docker-compose.yml",
        "sha256": hashlib.sha256(compose_path.read_bytes()).hexdigest(),
    }
    assert manifest["images"]["api"]["image_id"] == API_ID
    assert manifest["images"]["web"]["image_id"] == WEB_ID
    assert manifest["images"]["api"]["repository"] == "aios/api"
    assert manifest["images"]["web"]["repository"] == "aios/web"
    for component in ("api", "web"):
        assert manifest["images"][component]["tag"] == "v0.1.0"
        assert manifest["images"][component]["local_only"] is True
    archives = {rec["component"]: rec for rec in manifest["archives"]}
    assert set(archives) == {"api", "web"}
    for component, name in (("api", "aios-api-v0.1.0.tar"),
                            ("web", "aios-web-v0.1.0.tar")):
        raw = (package_dir / name).read_bytes()
        assert archives[component]["filename"] == name
        assert archives[component]["sha256"] == hashlib.sha256(raw).hexdigest()
        assert archives[component]["size_bytes"] == len(raw)
    assert manifest["build_context"]["root"] == "."
    assert manifest["build_context"]["api"]["dockerfile"] == "services/api/Dockerfile"
    assert manifest["build_context"]["web"]["dockerfile"] == "apps/web/Dockerfile"
    assert manifest["web_build_arg"] == {
        "NEXT_PUBLIC_API_BASE_URL": "http://127.0.0.1:8000"
    }
    assert manifest["scope"] == SCOPE
    assert manifest["boundary_note"] == BOUNDARY_NOTE
    assert "local" in manifest["boundary_note"].lower()
    assert "not" in manifest["boundary_note"].lower()
    assert "production readiness" in manifest["boundary_note"].lower()
    assert manifest["smoke"] == {"script": "infra/smoke_docker.sh", "result": "passed"}


def test_write_package_checksums_covers_archives_and_manifest(
    tmp_path: Path,
) -> None:
    """SHA256SUMS 恰覆盖两归档 + manifest 自身，且哈希独立重算一致。"""
    package_dir = _build_package(tmp_path)
    lines = (package_dir / CHECKSUMS_FILE).read_text(encoding="utf-8").splitlines()
    assert lines == sorted(lines, key=lambda ln: ln.split("  ", 1)[1])
    entries = {
        line.split("  ", 1)[1]: line.split("  ", 1)[0]
        for line in lines if line.strip()
    }
    assert set(entries) == {
        "aios-api-v0.1.0.tar", "aios-web-v0.1.0.tar", MANIFEST_FILE
    }
    for name, digest in entries.items():
        assert digest == hashlib.sha256(
            (package_dir / name).read_bytes()
        ).hexdigest()
    for line in lines:
        digest, name = line.split("  ", 1)
        assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_write_package_deterministic_without_generated_at(
    tmp_path: Path,
) -> None:
    """同一输入两次构建 manifest 字节一致（幂等可比对的基础）。"""
    first = _build_package(tmp_path / "one")
    second = _build_package(tmp_path / "two")
    assert (first / MANIFEST_FILE).read_bytes() == (
        second / MANIFEST_FILE).read_bytes()


def test_write_package_rejects_stray_entries_in_output_dir(
    tmp_path: Path,
) -> None:
    """目录内存在预期归档之外的条目拒绝混写，既有字节不变。"""
    package_dir = tmp_path / "artifacts" / "rc"
    package_dir.mkdir(parents=True)
    _seed_archives(package_dir)
    (package_dir / "operator-notes.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ReleaseCandidateError, match="拒绝混写"):
        write_release_package(
            package_dir,
            tag="v0.1.0",
            version_file=_version_file(tmp_path),
            git_commit=GIT_SHA,
            compose_file=_compose_file(tmp_path),
            api_image_id=API_ID,
            web_image_id=WEB_ID,
            api_archive="aios-api-v0.1.0.tar",
            web_archive="aios-web-v0.1.0.tar",
        )
    assert (package_dir / "operator-notes.txt").read_text(encoding="utf-8") == "keep"


def test_write_package_rejects_missing_or_symlink_archive(
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "artifacts" / "rc"
    package_dir.mkdir(parents=True)
    (package_dir / "aios-web-v0.1.0.tar").write_bytes(b"W")
    with pytest.raises(ReleaseCandidateError):
        write_release_package(
            package_dir, tag="v0.1.0", version_file=_version_file(tmp_path),
            git_commit=GIT_SHA, compose_file=_compose_file(tmp_path),
            api_image_id=API_ID, web_image_id=WEB_ID,
            api_archive="aios-api-v0.1.0.tar",
            web_archive="aios-web-v0.1.0.tar",
        )
    try:
        (package_dir / "aios-api-v0.1.0.tar").symlink_to(
            package_dir / "aios-web-v0.1.0.tar"
        )
    except (OSError, NotImplementedError):
        pytest.skip("此环境无法创建 symlink")
    with pytest.raises(ReleaseCandidateError):
        write_release_package(
            package_dir, tag="v0.1.0", version_file=_version_file(tmp_path),
            git_commit=GIT_SHA, compose_file=_compose_file(tmp_path),
            api_image_id=API_ID, web_image_id=WEB_ID,
            api_archive="aios-api-v0.1.0.tar",
            web_archive="aios-web-v0.1.0.tar",
        )


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"api_archive": "../evil.tar"}, "纯文件名"),
        ({"api_archive": "release-manifest.json"}, "保留文件名"),
        ({"api_archive": "same.tar", "web_archive": "same.tar"}, "必须不同"),
        ({"git_commit": "not-a-sha"}, "git commit"),
        ({"git_commit": "zzz" * 13}, "git commit"),
        ({"api_image_id": "sha256:xyz"}, "镜像 ID"),
        ({"web_image_id": "nothex"}, "镜像 ID"),
        ({"web_build_arg": ""}, "web build arg"),
        ({"build_context": ""}, "build context"),
        ({"generated_at": "  "}, "generated_at"),
    ],
)
def test_write_package_rejects_bad_inputs(
    tmp_path: Path, kwargs: dict, match: str
) -> None:
    package_dir = tmp_path / "artifacts" / "rc"
    package_dir.mkdir(parents=True)
    _seed_archives(package_dir)
    base = {
        "tag": "v0.1.0",
        "version_file": _version_file(tmp_path),
        "git_commit": GIT_SHA,
        "compose_file": _compose_file(tmp_path),
        "api_image_id": API_ID,
        "web_image_id": WEB_ID,
        "api_archive": "aios-api-v0.1.0.tar",
        "web_archive": "aios-web-v0.1.0.tar",
    }
    base.update(kwargs)
    with pytest.raises(ReleaseCandidateError, match=match):
        write_release_package(package_dir, **base)
    assert not (package_dir / MANIFEST_FILE).exists(), "拒绝时不得留下 manifest"


def test_write_package_failure_leaves_no_valid_partial(
    tmp_path: Path, monkeypatch
) -> None:
    """原子写失败（第二个文件 replace 失败）：最终文件全部清理、无 .tmp 残留。"""
    package_dir = tmp_path / "artifacts" / "rc"
    package_dir.mkdir(parents=True)
    _seed_archives(package_dir)
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated replace failure")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", flaky_replace)
    with pytest.raises(OSError):
        write_release_package(
            package_dir, tag="v0.1.0", version_file=_version_file(tmp_path),
            git_commit=GIT_SHA, compose_file=_compose_file(tmp_path),
            api_image_id=API_ID, web_image_id=WEB_ID,
            api_archive="aios-api-v0.1.0.tar",
            web_archive="aios-web-v0.1.0.tar",
        )
    leftovers = sorted(p.name for p in package_dir.iterdir())
    assert leftovers == ["aios-api-v0.1.0.tar", "aios-web-v0.1.0.tar"], leftovers
    assert not any(name.startswith(".") for name in leftovers), "无临时文件残留"
    assert not (package_dir / MANIFEST_FILE).exists()
    assert not (package_dir / CHECKSUMS_FILE).exists()
    assert verify_release_package(package_dir)["ok"] is False


# --- 4. verify 独立校验（不加载 Docker 镜像）-------------------------------------


def test_verify_ok_on_fresh_package(tmp_path: Path) -> None:
    package_dir = _build_package(tmp_path)
    report = verify_release_package(
        package_dir, version_file=_version_file(tmp_path)
    )
    assert report["ok"] is True
    assert report["problems"] == []
    passed = {c["check"] for c in report["checks"] if c["result"] == "pass"}
    assert {
        "version_tag_consistency", "version_file_consistency",
        "checksums_coverage", "archive_hash",
    } <= passed


def test_verify_detects_tampered_archive(tmp_path: Path) -> None:
    package_dir = _build_package(tmp_path)
    (package_dir / "aios-api-v0.1.0.tar").write_bytes(b"TAMPERED-BYTES")
    report = verify_release_package(package_dir)
    assert report["ok"] is False
    joined = " ".join(report["problems"])
    assert "SHA256SUMS 不符" in joined
    assert "manifest 声明不符" in joined


def test_verify_detects_tampered_manifest_bytes(tmp_path: Path) -> None:
    """直接改 manifest 字节（不动 SHA256SUMS）：manifest 哈希门失败。"""
    package_dir = _build_package(tmp_path)
    manifest = _load_manifest(package_dir)
    manifest["version"] = "9.9.9"
    (package_dir / MANIFEST_FILE).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = verify_release_package(package_dir)
    assert report["ok"] is False
    assert any(
        c["check"] == "archive_hash" and MANIFEST_FILE in c["detail"]
        for c in report["checks"] if c["result"] == "fail"
    )


def test_verify_detects_tampered_checksums_file(tmp_path: Path) -> None:
    package_dir = _build_package(tmp_path)
    lines = (package_dir / CHECKSUMS_FILE).read_text(encoding="utf-8").splitlines()
    flipped = list(lines)
    flipped[0] = "0" * 64 + flipped[0][64:]
    (package_dir / CHECKSUMS_FILE).write_text(
        "\n".join(flipped) + "\n", encoding="utf-8"
    )
    report = verify_release_package(package_dir)
    assert report["ok"] is False
    assert any("SHA256SUMS 不符" in p for p in report["problems"])


def test_verify_detects_missing_checksums_entry(tmp_path: Path) -> None:
    package_dir = _build_package(tmp_path)
    lines = [
        line for line in
        (package_dir / CHECKSUMS_FILE).read_text(encoding="utf-8").splitlines()
        if MANIFEST_FILE not in line
    ]
    (package_dir / CHECKSUMS_FILE).write_text("\n".join(lines) + "\n",
                                              encoding="utf-8")
    report = verify_release_package(package_dir)
    assert report["ok"] is False
    assert any(c["check"] == "checksums_coverage" for c in report["checks"])


def test_verify_detects_extra_checksums_entry(tmp_path: Path) -> None:
    package_dir = _build_package(tmp_path)
    text = (package_dir / CHECKSUMS_FILE).read_text(encoding="utf-8")
    (package_dir / CHECKSUMS_FILE).write_text(
        text + "0" * 64 + "  phantom.tar\n", encoding="utf-8"
    )
    report = verify_release_package(package_dir)
    assert report["ok"] is False
    assert any(
        c["check"] == "checksums_coverage" and "多" in c["detail"]
        for c in report["checks"]
    )


def test_verify_detects_malformed_and_duplicate_checksum_lines(
    tmp_path: Path,
) -> None:
    package_dir = _build_package(tmp_path)
    lines = (package_dir / CHECKSUMS_FILE).read_text(encoding="utf-8").splitlines()
    broken = [lines[0], "garbage line", lines[0]]
    (package_dir / CHECKSUMS_FILE).write_text(
        "\n".join(broken) + "\n", encoding="utf-8"
    )
    report = verify_release_package(package_dir)
    assert report["ok"] is False
    joined = " ".join(report["problems"])
    assert "不合形态" in joined or "重复条目" in joined


def test_verify_detects_missing_archive_file(tmp_path: Path) -> None:
    package_dir = _build_package(tmp_path)
    (package_dir / "aios-web-v0.1.0.tar").unlink()
    report = verify_release_package(package_dir)
    assert report["ok"] is False
    assert any("文件缺失" in p for p in report["problems"])


def test_verify_detects_uncovered_extra_file_in_package(tmp_path: Path) -> None:
    package_dir = _build_package(tmp_path)
    (package_dir / "notes.txt").write_text("stray", encoding="utf-8")
    report = verify_release_package(package_dir)
    assert report["ok"] is False
    assert any("未被校验和覆盖" in p for p in report["problems"])


def test_verify_detects_version_file_drift(tmp_path: Path) -> None:
    """manifest 内部自洽，但与 VERSION 文件不一致 => 失败（三方一致性）。"""
    package_dir = _build_package(tmp_path)

    def mutate(manifest: dict) -> None:
        manifest["version"] = "0.2.0"

    _rewrite_manifest_and_sums(package_dir, mutate)
    report = verify_release_package(
        package_dir, version_file=_version_file(tmp_path, "0.1.0")
    )
    assert report["ok"] is False
    assert any(
        c["check"] == "version_file_consistency" for c in report["checks"]
    )


def test_verify_missing_manifest_or_sums(tmp_path: Path) -> None:
    package_dir = _build_package(tmp_path)
    (package_dir / MANIFEST_FILE).unlink()
    report = verify_release_package(package_dir)
    assert report["ok"] is False
    assert any(c["check"] == "manifest_present" for c in report["checks"])
    package_dir2 = _build_package(tmp_path / "two")
    (package_dir2 / CHECKSUMS_FILE).unlink()
    report2 = verify_release_package(package_dir2)
    assert report2["ok"] is False
    assert any(c["check"] == "checksums_present" for c in report2["checks"])


def test_verify_rejects_broken_json_manifest(tmp_path: Path) -> None:
    package_dir = _build_package(tmp_path)
    (package_dir / MANIFEST_FILE).write_text("{not json", encoding="utf-8")
    report = verify_release_package(package_dir)
    assert report["ok"] is False
    assert any(c["check"] == "manifest_json" for c in report["checks"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.pop("git_commit"),
        lambda m: m.pop("images"),
        lambda m: m.pop("compose"),
        lambda m: m.pop("archives"),
        lambda m: m.pop("boundary_note"),
        lambda m: m.pop("build_context"),
        lambda m: m.pop("web_build_arg"),
        lambda m: m.pop("smoke"),
        lambda m: m.pop("checksums_file"),
        lambda m: m.pop("scope"),
        lambda m: m.update({"schema_version": 2}),
        lambda m: m.update({"package_kind": "production-release"}),
        lambda m: m.update({"version": "1.2.3"}),
        lambda m: m.update({"tag": "v9.9.9"}),
        lambda m: m.update({"tag": "1.2.3"}),
        lambda m: m["images"]["web"].update({"tag": "v0.0.9"}),
        lambda m: m["images"]["api"].update({"image_id": "not-a-digest"}),
        lambda m: m["archives"][0].update({"sha256": "XYZ"}),
        lambda m: m["archives"][0].update({"size_bytes": -1}),
        lambda m: m["archives"].append(dict(m["archives"][0])),
        lambda m: m.update({"scope": "production-release"}),
        lambda m: m.update({"boundary_note": "all good, ship it"}),
        lambda m: m.update({"web_build_arg": {"WRONG": "x"}}),
        lambda m: m.update({"checksums_file": "MD5SUMS"}),
    ],
)
def test_verify_schema_gate_catches_self_consistent_tampering(
    tmp_path: Path, mutate
) -> None:
    """自洽篡改：改 manifest 后重算 SHA256SUMS（字节哈希门失效）——
    schema/一致性门必须仍能拦下（缺字段/坏类型/版本错位/边界声明缺失）。"""
    package_dir = _build_package(tmp_path)
    _rewrite_manifest_and_sums(package_dir, mutate)
    report = verify_release_package(
        package_dir, version_file=_version_file(tmp_path)
    )
    assert report["ok"] is False, report["problems"]


# --- 5. 零敏感：产物无凭据字样；模块零 env/网络/DB/子进程 -----------------------


def test_package_outputs_contain_no_secret_markers(tmp_path: Path) -> None:
    """manifest/SHA256SUMS/写包 report 无密码/token/key marker、无凭据 URL 形态。"""
    package_dir = tmp_path / "artifacts" / "rc"
    package_dir.mkdir(parents=True)
    _seed_archives(package_dir, api_bytes=b"api", web_bytes=b"web")
    report = write_release_package(
        package_dir, tag="v0.1.0", version_file=_version_file(tmp_path),
        git_commit=GIT_SHA, compose_file=_compose_file(tmp_path),
        api_image_id=API_ID, web_image_id=WEB_ID,
        api_archive="aios-api-v0.1.0.tar", web_archive="aios-web-v0.1.0.tar",
    )
    dumped = json.dumps(report, ensure_ascii=False)
    for name in (MANIFEST_FILE, CHECKSUMS_FILE):
        text = (package_dir / name).read_text(encoding="utf-8")
        assert PASSWORD_MARKER not in text
        assert API_KEY_MARKER not in text
        assert not _CREDENTIAL_URL_RE.search(text), name
    assert PASSWORD_MARKER not in dumped
    assert API_KEY_MARKER not in dumped


def test_module_has_no_env_subprocess_db_or_network_surface() -> None:
    """AST 级守卫：零 os.environ / 零 subprocess/网络/DB 导入（verify 不可能
    加载 Docker 镜像——模块根本没有执行命令或连网的通道）。"""
    import app.ops.release_candidate as rc

    tree = ast.parse(Path(rc.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {(node.module or "").split(".")[0]}
        else:
            names = set()
        assert names.isdisjoint(
            {"subprocess", "socket", "httpx", "requests", "urllib",
             "asyncio", "asyncpg", "psycopg", "psycopg2", "sqlalchemy",
             "aiohttp", "docker"}
        ), f"模块不得导入 {names}"
        if isinstance(node, ast.Attribute) and node.attr == "environ":
            raise AssertionError("模块不得访问环境变量（os.environ）")
        if isinstance(node, ast.Name) and node.id == "create_engine":
            raise AssertionError("模块不得创建数据库引擎")


# --- 6. CLI 注册与行为面 ---------------------------------------------------------


def _cli(action: str, **overrides) -> int:
    base = {
        "action": action, "output_dir": None, "package_dir": None, "tag": None,
        "version_file": None, "git_commit": None, "compose_file": None,
        "api_image_id": None, "web_image_id": None, "api_archive": None,
        "web_archive": None, "build_context": ".",
        "web_build_arg": "http://127.0.0.1:8000",
        "smoke_script": "infra/smoke_docker.sh", "generated_at": None,
    }
    base.update(overrides)
    return cli_module._run_release_candidate(SimpleNamespace(**base))


def test_cli_registered_in_module() -> None:
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    assert '"release-candidate"' in source
    assert hasattr(cli_module, "_run_release_candidate")


def test_cli_manifest_writes_package_and_prints_boundary(
    tmp_path: Path, capsys
) -> None:
    package_dir = tmp_path / "artifacts" / "rc"
    package_dir.mkdir(parents=True)
    _seed_archives(package_dir)
    code = _cli(
        "manifest",
        output_dir=str(package_dir),
        tag="v0.1.0",
        version_file=str(_version_file(tmp_path)),
        git_commit=GIT_SHA,
        compose_file=str(_compose_file(tmp_path)),
        api_image_id=API_ID,
        web_image_id=WEB_ID,
        api_archive="aios-api-v0.1.0.tar",
        web_archive="aios-web-v0.1.0.tar",
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "不是 production readiness 声明" in out
    assert verify_release_package(package_dir)["ok"] is True


def test_cli_manifest_missing_args_exit_2(tmp_path: Path, capsys) -> None:
    assert _cli("manifest") == 2
    assert "缺少必填参数" in capsys.readouterr().out
    assert _cli("manifest", output_dir=str(tmp_path / "artifacts" / "rc")) == 2


def test_cli_manifest_guardrail_exit_2_no_side_effects(
    tmp_path: Path, capsys
) -> None:
    assert (
        _cli(
            "manifest",
            output_dir=str(tmp_path / "outside" / "rc"),
            tag="v0.1.0",
            version_file=str(_version_file(tmp_path)),
            git_commit=GIT_SHA,
            compose_file=str(_compose_file(tmp_path)),
            api_image_id=API_ID,
            web_image_id=WEB_ID,
            api_archive="a.tar",
            web_archive="b.tar",
        )
        == 2
    )
    assert "拒绝" in capsys.readouterr().out
    assert not (tmp_path / "outside").exists()


def test_cli_verify_exit_codes(tmp_path: Path, capsys) -> None:
    package_dir = _build_package(tmp_path)
    assert (
        _cli("verify", package_dir=str(package_dir),
             version_file=str(_version_file(tmp_path)))
        == 0
    )
    assert "VERIFY OK" in capsys.readouterr().out
    (package_dir / "aios-api-v0.1.0.tar").write_bytes(b"tampered")
    assert _cli("verify", package_dir=str(package_dir)) == 1
    assert "VERIFY FAILED" in capsys.readouterr().out
    assert _cli("verify") == 2
    assert _cli("verify", package_dir=str(tmp_path / "nope")) == 2


def test_cli_version_mismatch_exit_2(tmp_path: Path, capsys) -> None:
    package_dir = tmp_path / "artifacts" / "rc"
    package_dir.mkdir(parents=True)
    _seed_archives(package_dir)
    assert (
        _cli(
            "manifest",
            output_dir=str(package_dir),
            tag="v0.1.0",
            version_file=str(_version_file(tmp_path, "9.9.9")),
            git_commit=GIT_SHA,
            compose_file=str(_compose_file(tmp_path)),
            api_image_id=API_ID,
            web_image_id=WEB_ID,
            api_archive="aios-api-v0.1.0.tar",
            web_archive="aios-web-v0.1.0.tar",
        )
        == 2
    )
    assert not (package_dir / MANIFEST_FILE).exists()


# --- 7. build 脚本契约（源码级 + bash -n；不调用真实 Docker）---------------------

FORBIDDEN_CODE_PATTERNS = (
    "git tag", "git push", "docker push", "docker login", "gh release",
    "gh run", "kubectl", "helm ", "terraform ", "ansible-playbook",
    "aws ", "gcloud ", "az ", "ssh ", "scp ",
)


def _script_code_lines() -> list[str]:
    """脚本源码去掉注释/空行——禁止项只看真实代码行。"""
    lines = BUILD_SCRIPT.read_text(encoding="utf-8").splitlines()
    code = [
        ln for ln in lines
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert code, "build 脚本为空"
    return code


def test_build_script_exists_and_passes_bash_n() -> None:
    assert BUILD_SCRIPT.is_file(), "缺少 infra/build_release_candidate.sh"
    if BASH is None:
        pytest.skip("bash 不可用（语法检查需要 bash）")
    result = subprocess.run(
        [BASH, "-n", BUILD_SCRIPT_RELATIVE],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_build_script_contract_texts() -> None:
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    # tag 严格形态 + VERSION 逐字一致 + 干净 worktree + 完整 SHA
    assert re.search(r"\^v\(0\|\[1-9\]\[0-9\]\*\)", source)
    assert 'tr -d ' in source and "VERSION" in source
    assert "git status --porcelain" in source
    assert "git rev-parse HEAD" in source
    # 镜像：同源构建、钉本地 tag、独立归档
    assert "services/api/Dockerfile" in source
    assert "apps/web/Dockerfile" in source
    assert '"aios/api:$TAG"' in source
    assert '"aios/web:$TAG"' in source
    assert "--build-arg \"NEXT_PUBLIC_API_BASE_URL=$WEB_BUILD_ARG\"" in source
    assert "docker save -o" in source
    # compose：AIOS_IMAGE_TAG + --no-build + 既有冒烟脚本 + 安全清理
    assert 'AIOS_IMAGE_TAG="$TAG"' in source
    assert "up -d --no-build" in source
    assert "smoke_docker.sh" in source
    assert "down --remove-orphans" in source
    assert "trap cleanup EXIT" in source
    # manifest 助手与独立 verify
    assert "release-candidate manifest" in source
    assert "release-candidate verify" in source
    # 输出目录护栏：仓库 artifacts/temp 前缀 + symlink 组件检查
    assert '"$REPO_ROOT/artifacts"/*|"$REPO_ROOT/temp"/*' in source
    assert '[ ! -L "$partial" ]' in source


def test_build_script_never_deletes_volumes() -> None:
    """down 绝不带 -v/--volumes——用户卷不可删除（含 rm -rf 只允许用于
    本运行创建且无 manifest 的包目录）。"""
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    for line in source.splitlines():
        code = line.split("#", 1)[0]
        assert "down -v" not in code, line
        assert "down --volumes" not in code, line
        assert "volume rm" not in code, line
        assert "volume prune" not in code, line
        assert "system prune" not in code, line


def test_build_script_has_no_publish_or_remote_surface() -> None:
    """真实代码行零发布/远端动词：不打 tag、不 push、不发 Release、不登 registry、
    不碰集群/云/SSH。"""
    for line in _script_code_lines():
        code = line.strip()
        for pattern in FORBIDDEN_CODE_PATTERNS:
            assert pattern not in code, f"脚本代码行含禁止动词 {pattern!r}: {line}"
        assert "curl " not in code or "127.0.0.1" in code, (
            f"外部请求只允许本机: {line}"
        )


def test_build_script_smoke_override_is_test_seam_with_safe_default() -> None:
    """冒烟脚本可被 AIOS_RELEASE_SMOKE_SCRIPT 覆盖（测试替身通道），
    缺省必须是 infra/smoke_docker.sh。"""
    source = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert '${AIOS_RELEASE_SMOKE_SCRIPT:-infra/smoke_docker.sh}' in source


def test_workflow_and_script_declare_local_scope_boundary() -> None:
    """脚本与工作流都显式声明：本地 RC、非 production readiness、
    不推仓库/不打 tag/不发 Release。"""
    script = BUILD_SCRIPT.read_text(encoding="utf-8")
    workflow = WORKFLOW_FILE.read_text(encoding="utf-8")
    for text in (script, workflow):
        assert "production readiness" in text
        lowered = text.lower()
        assert "local" in lowered
    assert "不推" in script or "不发布" in script


# --- 8. build 脚本行为面（stub git/docker/python；不调用真实 Docker）-------------


def _bash_path(path: Path | str) -> str | None:
    """Windows 路径转 bash 视角路径（cygpath/wslpath）；POSIX 路径原样；
    无法转换且是 Windows 形态 => None（调用方 skip）。"""
    text = str(path)
    if not re.match(r"^[A-Za-z]:", text):
        return text
    if BASH is None:
        return None
    for tool in ("cygpath -u", "wslpath -u"):
        result = subprocess.run(
            [BASH, "-c", f'{tool} "$1"', "bash", text],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


class _StubEnv:
    """git/docker/python3 桩 + 调用日志 + 真实 python 委派。"""

    def __init__(self, tmp_path: Path, *, real_python: bool = True,
                 dirty: bool = False, smoke_exit: int = 0) -> None:
        self.root = REPO_ROOT / "temp" / f"rc-stub-{os.getpid()}-{id(self):x}"
        self.bin = self.root / "bin"
        self.log = self.root / "calls.log"
        self.fake_smoke = self.root / "fake-smoke.sh"
        self.bin.mkdir(parents=True, exist_ok=True)
        self.dirty = dirty
        self.smoke_exit = smoke_exit
        self._real_python = real_python
        real_py = _bash_path(sys.executable)
        if real_python and real_py is None:
            pytest.skip("无法把 Windows python 转成 bash 路径（缺 cygpath/wslpath）")
        self._real_py = real_py or ""
        log_path = _bash_path(self.log)
        assert log_path is not None
        self._log_path = log_path
        self._write_stubs()
        self.bin_posix = _bash_path(self.bin)
        self.smoke_posix = _bash_path(self.fake_smoke)
        assert self.bin_posix and self.smoke_posix

    def _stub(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(body, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def _write_stubs(self) -> None:
        log = self._log_path
        dirty = "true" if self.dirty else "false"
        a40 = "a" * 40
        self._stub(
            "git",
            "#!/usr/bin/env bash\n"
            f'echo "git $*" >> "{log}"\n'
            "case \"$1 $2\" in\n"
            '  "status --porcelain")\n'
            f"    if [ {dirty} = true ]; then printf ' M dirty.py\\n'; fi\n"
            "    exit 0 ;;\n"
            f"  \"rev-parse HEAD\") printf '{a40}\\n'; exit 0 ;;\n"
            '  "rev-parse --is-inside-work-tree") echo true; exit 0 ;;\n'
            "esac\n"
            "exit 0\n",
        )
        b64 = "b" * 64
        c64 = "c" * 64
        self._stub(
            "docker",
            "#!/usr/bin/env bash\n"
            f'echo "docker $*" >> "{log}"\n'
            "if [ \"$1\" = build ]; then exit 0; fi\n"
            "if [ \"$1 $2\" = \"image inspect\" ]; then\n"
            "  case \"$*\" in\n"
            f"    *aios/api*) printf 'sha256:{b64}\\n' ;;\n"
            f"    *) printf 'sha256:{c64}\\n' ;;\n"
            "  esac\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1\" = compose ]; then exit 0; fi\n"
            "if [ \"$1\" = save ]; then\n"
            "  out=\"\"; prev=\"\"\n"
            "  for a in \"$@\"; do if [ \"$prev\" = -o ]; then out=\"$a\"; fi; "
            "prev=\"$a\"; done\n"
            "  printf \"stub-image-%s\" \"$4\" > \"$out\"\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
        )
        if self._real_python:
            self._stub(
                "python3",
                "#!/usr/bin/env bash\n"
                f'echo "python3 $*" >> "{log}"\n'
                f'exec "{self._real_py}" "$@"\n',
            )
        else:
            self._stub(
                "python3",
                "#!/usr/bin/env bash\n"
                f'echo "python3 $*" >> "{log}"\n'
                "exit 0\n",
            )
        self.fake_smoke.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "SMOKE-STUB exit={self.smoke_exit}" >> "{log}"\n'
            f"exit {self.smoke_exit}\n",
            encoding="utf-8", newline="\n",
        )
        self.fake_smoke.chmod(0o755)

    def run_builder(self, *args: str) -> subprocess.CompletedProcess:
        assert BASH is not None
        exports = (
            f'export PATH="{self.bin_posix}:$PATH"; '
            f'export AIOS_RELEASE_SMOKE_SCRIPT="{self.smoke_posix}"'
        )
        joined = " ".join(args)
        return subprocess.run(
            [BASH, "-c", f"{exports}; bash {BUILD_SCRIPT_RELATIVE} {joined}"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=180,
            check=False,
        )

    def calls(self) -> list[str]:
        if not self.log.exists():
            return []
        return [
            ln for ln in self.log.read_text(encoding="utf-8").splitlines() if ln
        ]

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


@pytest.fixture
def stub_env(tmp_path: Path):
    env = _StubEnv(tmp_path)
    yield env
    env.cleanup()


def _version_tag() -> str:
    """读真实仓库 VERSION 组装 tag（行为面测试与真实 VERSION 保持一致）。"""
    return "v" + "".join((REPO_ROOT / "VERSION").read_text(encoding="utf-8").split())


def _bumped_tag() -> str:
    """与 VERSION 一定不一致的合法形态 tag（tag 失配早退测试用）。"""
    parts = _version_tag()[1:].split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return "v" + ".".join(parts)


def _out_rel(env: _StubEnv) -> str:
    """stub 环境内的包输出目录（仓库 temp/ 护栏内的相对 POSIX 路径）。"""
    return env.root.relative_to(REPO_ROOT).as_posix() + "/out"


@pytest.mark.skipif(BASH is None, reason="bash 不可用")
def test_build_script_happy_path_full_sequence(stub_env) -> None:
    """快乐路径全序列：tag/git 校验 → 双镜像 build → compose up --no-build →
    冒烟 → down --remove-orphans → docker save ×2 → manifest + verify；
    包内恰四文件且真实 verify ok。"""
    tag = _version_tag()
    result = stub_env.run_builder("--tag", tag, "--output-dir", _out_rel(stub_env))
    assert result.returncode == 0, result.stdout + result.stderr
    calls = stub_env.calls()
    joined = "\n".join(calls)
    assert "git status --porcelain" in joined
    assert "git rev-parse HEAD" in joined
    assert any(
        "docker build" in c and "services/api/Dockerfile" in c for c in calls
    ), "必须以 services/api/Dockerfile 构建 api 镜像"
    assert any(
        "docker build" in c and "apps/web/Dockerfile" in c for c in calls
    ), "必须以 apps/web/Dockerfile 构建 web 镜像"
    assert any(
        "docker build" in c and "NEXT_PUBLIC_API_BASE_URL=" in c for c in calls
    ), "web 构建必须带 NEXT_PUBLIC_API_BASE_URL build arg"
    assert any("up -d --no-build" in c for c in calls)
    assert "SMOKE-STUB exit=0" in joined
    assert any("down --remove-orphans" in c for c in calls)
    assert not any("down -v " in c or c.endswith("down -v") for c in calls)
    assert any(
        "docker save -o" in c and f"aios-api-{tag}.tar" in c for c in calls
    )
    assert any(
        "docker save -o" in c and f"aios-web-{tag}.tar" in c for c in calls
    )
    assert any("release-candidate manifest" in c for c in calls)
    assert any("release-candidate verify" in c for c in calls)
    out_dir = stub_env.root / "out"
    assert sorted(p.name for p in out_dir.iterdir()) == sorted([
        f"aios-api-{tag}.tar", f"aios-web-{tag}.tar",
        CHECKSUMS_FILE, MANIFEST_FILE,
    ])
    report = verify_release_package(out_dir, version_file=REPO_ROOT / "VERSION")
    assert report["ok"] is True, report["problems"]


@pytest.mark.skipif(BASH is None, reason="bash 不可用")
def test_build_script_dirty_worktree_exits_before_docker(tmp_path: Path) -> None:
    env = _StubEnv(tmp_path, dirty=True)
    try:
        result = env.run_builder("--tag", _version_tag(), "--output-dir", _out_rel(env))
        assert result.returncode == 1
        assert "不干净" in (result.stdout + result.stderr)
        calls = env.calls()
        assert not any(c.startswith("docker") for c in calls), calls
        assert not any(c.startswith("python3") for c in calls), calls
        assert not (env.root / "out").exists()
    finally:
        env.cleanup()


@pytest.mark.skipif(BASH is None, reason="bash 不可用")
def test_build_script_tag_version_mismatch_exits_before_git(tmp_path: Path) -> None:
    env = _StubEnv(tmp_path)
    try:
        result = env.run_builder("--tag", _bumped_tag(), "--output-dir", _out_rel(env))
        assert result.returncode == 1
        assert "不一致" in (result.stdout + result.stderr)
        calls = env.calls()
        assert not any(c.startswith("docker") for c in calls), calls
        assert not any(c.startswith("git") for c in calls), calls
    finally:
        env.cleanup()


@pytest.mark.skipif(BASH is None, reason="bash 不可用")
def test_build_script_rejects_nonempty_output_dir_untouched(
    tmp_path: Path,
) -> None:
    """输出目录非空拒绝且不动既有文件（拒绝发生在任何 docker 调用之前）。"""
    env = _StubEnv(tmp_path)
    try:
        out = env.root / "out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "operator-notes.txt").write_text("keep", encoding="utf-8")
        result = env.run_builder("--tag", _version_tag(), "--output-dir", _out_rel(env))
        assert result.returncode == 1
        assert "拒绝混写" in (result.stdout + result.stderr)
        assert (out / "operator-notes.txt").read_text(encoding="utf-8") == "keep"
        assert sorted(p.name for p in out.iterdir()) == ["operator-notes.txt"]
        assert not any("docker build" in c for c in env.calls())
    finally:
        env.cleanup()


@pytest.mark.skipif(BASH is None, reason="bash 不可用")
def test_build_script_rejects_output_dir_outside_guardrail(
    tmp_path: Path,
) -> None:
    env = _StubEnv(tmp_path)
    try:
        result = env.run_builder("--tag", _version_tag(), "--output-dir", "outside-pack/rc")
        assert result.returncode == 1
        assert "artifacts/ 或 temp/" in (result.stdout + result.stderr)
        assert not any("docker build" in c for c in env.calls())
        assert not (REPO_ROOT / "outside-pack").exists()
    finally:
        env.cleanup()


@pytest.mark.skipif(BASH is None, reason="bash 不可用")
def test_build_script_smoke_failure_cleans_compose_and_leaves_no_package(
    tmp_path: Path,
) -> None:
    """冒烟失败：compose 项目被清理（down --remove-orphans），且不留下任何
    包目录/归档/manifest 半成品。"""
    env = _StubEnv(tmp_path, smoke_exit=1)
    try:
        result = env.run_builder("--tag", _version_tag(), "--output-dir", _out_rel(env))
        assert result.returncode != 0
        calls = env.calls()
        assert "SMOKE-STUB exit=1" in "\n".join(calls)
        assert any("down --remove-orphans" in c for c in calls), (
            "失败路径必须清理 compose 项目"
        )
        out = env.root / "out"
        assert not out.exists(), "冒烟失败不得创建包目录"
        assert not any("release-candidate manifest" in c for c in calls)
    finally:
        env.cleanup()


# --- 9. 工作流静态契约（仅 workflow_dispatch 手动触发）--------------------------


def _workflow_yaml() -> dict:
    import yaml

    data = yaml.safe_load(WORKFLOW_FILE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _workflow_code_text() -> str:
    """workflow 非 comment 行（触发器/权限/step 动词只看真实配置）。"""
    return "\n".join(
        ln
        for ln in WORKFLOW_FILE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    )


def test_workflow_triggers_are_workflow_dispatch_only() -> None:
    """`on:` 只允许 workflow_dispatch——出现 push/pull_request/schedule 等
    任何自动触发器即红。"""
    data = _workflow_yaml()
    triggers = data.get("on", data.get(True))
    assert isinstance(triggers, dict), f"on 必须是 mapping: {triggers!r}"
    assert set(triggers) == {"workflow_dispatch"}, (
        f"只允许 workflow_dispatch 触发，发现: {sorted(triggers)}"
    )
    tag_input = triggers["workflow_dispatch"].get("inputs", {}).get("tag", {})
    assert tag_input.get("required") is False, "tag 输入必须可选（留空取 VERSION）"


def test_workflow_minimal_permissions_linux_runner_artifact_upload() -> None:
    data = _workflow_yaml()
    assert data.get("permissions") == {"contents": "read"}, "必须最小权限 contents: read"
    jobs = data.get("jobs")
    assert isinstance(jobs, dict) and jobs
    code = _workflow_code_text()
    for job in jobs.values():
        assert "ubuntu" in str(job.get("runs-on", "")), "必须在 Linux runner 运行"
        steps = json.dumps(job.get("steps", []), ensure_ascii=False)
        assert "actions/upload-artifact" in steps, "产物必须走 upload-artifact"
    lowered = code.lower()
    for verb in (
        "docker push", "docker login", "git push", "git tag", "gh release",
        "kubectl", "helm ", "terraform ", "ansible-playbook", "aws ", "gcloud ",
        "az ", "ssh ", "scp ", "release/create", "packages/write",
    ):
        assert verb not in lowered, f"workflow 配置行含发布动词 {verb!r}"


# --- 10. 真实 Docker 构建冒烟（仅 AIOS_RELEASE_SMOKE=1 显式开启）----------------


def test_real_docker_release_build_smoke_opt_in() -> None:
    """真实 Docker 全链路 RC 构建（真镜像 + compose 冒烟 + manifest + verify）。
    仅在 AIOS_RELEASE_SMOKE=1 时执行（默认 skip）；需要干净 worktree 与本地
    Docker——本测试是操作员在发布前的显式验证通道，不是 CI 依赖。"""
    if os.environ.get("AIOS_RELEASE_SMOKE") != "1":
        pytest.skip("真实 Docker RC 构建冒烟仅在 AIOS_RELEASE_SMOKE=1 时执行")
    if BASH is None:
        pytest.skip("bash 不可用")
    tag = _version_tag()
    out_rel = f"temp/rc-real-{tag}"
    out_dir = REPO_ROOT / "temp" / f"rc-real-{tag}"
    shutil.rmtree(out_dir, ignore_errors=True)
    try:
        result = subprocess.run(
            [BASH, BUILD_SCRIPT_RELATIVE, "--tag", tag, "--output-dir", out_rel],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        report = verify_release_package(out_dir, version_file=REPO_ROOT / "VERSION")
        assert report["ok"] is True, report["problems"]
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
