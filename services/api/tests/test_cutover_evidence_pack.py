"""M10-16 cutover evidence pack：脚手架/手册/模板防误用/审批 DRAFT 底稿。

覆盖矩阵：
1. 13 步无漏项：build_pack_files 恰为 13 步模板 + 锚副本模板 + README；
   MANUAL_ENTRIES / FIXTURE_FORMS / TEMPLATE_FIELDS 与
   cutover_rehearsal.STEPS 一一对应；README 逐步含 step_id、证据文件名与
   来源/脱敏/语义/授权/fixture 六要素，并含审批 SHA-256 计算、隔离 fixture
   全链路与「不代表生产验收 / 人工逐项授权」声明；
2. 模板防误用：模板文件名不与 rehearsal 的 KNOWN_EVIDENCE_FILES 相交；
   脚手架目录本身跑 rehearsal => NOT READY（13 步全 not_executed，模板只进
   unrecognized_files）；模板改名直用 => 13 步全 blocked（REPLACE-ME 类型/
   枚举故意不符，绝无 pass 读法）；锚副本模板改名 => blocked；
3. 路径安全与幂等/拒绝覆盖：artifacts/temp 护栏外拒绝、`..` 越界拒绝、
   目标是 artifacts/temp 本身拒绝、已存在普通文件拒绝、symlink 目标与
   symlink 祖先拒绝（无 symlink 权限环境 skip）；已存在目录非空时必须与
   本工具脚手架逐字节一致（幂等零改写），多余文件/被改模板/子目录一律
   拒绝且既有字节不变；空目录可写入；
4. 零敏感：模板 JSON 无敏感键、无内嵌凭据；全部产物无 `://user:pass@`
   形态字样；approval-draft 输出不回显敏感值（marker 零泄漏，含 CLI 打印
   与 --output 落盘文件）；
5. approval-draft：哈希与文件字节精确一致（sha256 独立重算比对）；缺文件
   如实列 step_evidence_missing；cutover-approval.json 自身不计入绑定哈希
   （approval_file_present 如实标注）；锚副本计入 supporting_evidence；
   证据变更后旧底稿哈希失配（以旧底稿构建的审批 rehearsal 判 blocked、以
   新底稿构建的 pass）；底稿直接改名为 cutover-approval.json => blocked
   不会被误判 ready；**DRAFT 不可审批边界（返工）——底稿补齐 step 与全部
   合法人工字段、哈希精确匹配但保留 draft 专用元数据字段 => cutover-approval
   blocked 且整体 not ready（rehearsal 审批评估器对 APPROVAL_DRAFT_RESERVED_FIELDS
   fail-closed），移除全部 draft 专用元数据后 => pass/ready；只保留单个
   元数据字段同样 blocked；拒绝名单与底稿输出字段集合同步（漏登记即红）**；
   --output 复用 artifacts/temp 护栏，越界拒绝；
6. 文档化隔离 fixture 全链路：按 FIXTURE_FORMS 构造 12 步证据 + 底稿哈希
   + 人工字段组装审批 => rehearsal_ready=True（fixture 值非真实执行，仅
   演练——README 已声明不代表生产验收）；
7. CLI 注册与行为面：子命令注册、无 --yes 执行形态（argparse exit 2）、
   main 分发 scaffold/approval-draft、缺参 exit 2；模块源码零 os.environ/
   零 DB/零网络引用。

全部测试只用临时目录与本地文件，不连接任何数据库、不发任何网络请求。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ops import cli as cli_module
from app.ops import cutover_evidence_pack as cep
from app.ops.cutover_evidence_pack import (
    FIXTURE_FORMS,
    MANUAL_APPROVAL_FIELDS,
    MANUAL_ENTRIES,
    README_FILE,
    TEMPLATE_FIELDS,
    PackInputError,
    build_approval_draft,
    build_pack_files,
    build_step_template,
    render_pack_readme,
    resolve_scaffold_target,
    scaffold_pack,
    template_name,
)
from app.ops.cutover_rehearsal import (
    ANCHOR_COMPANION_FILE,
    APPROVAL_DRAFT_RESERVED_FIELDS,
    KNOWN_EVIDENCE_FILES,
    STEPS,
    run_cutover_rehearsal,
)
from app.ops.evidence_kit import find_embedded_credential, find_sensitive_key

#: 敏感值 marker：任何产物（模板/手册/底稿/CLI 打印/落盘文件）都不得包含
PASSWORD_MARKER = "PROD-PW-88d3"
API_KEY_MARKER = "sk-PROD-KEY-88e4"
CREDENTIAL_URL_MARKER = "postgres://alice:PROD-PW-88d3@db.example.com/prod"

_NON_APPROVAL_STEPS = [spec for spec in STEPS if spec.step_id != "cutover-approval"]
_CREDENTIAL_URL_RE = re.compile(r"://\S+:\S+@")


def _artifacts_pack_dir(tmp_path: Path, name: str = "pack1") -> Path:
    """护栏内的合法脚手架目标：tmp_path/artifacts/<name>（父目录名=artifacts）。"""
    return tmp_path / "artifacts" / name


def _write_json(directory: Path, name: str, payload) -> None:
    (directory / name).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _fixture_payload(step_id: str) -> dict:
    """从 FIXTURE_FORMS 提取可解析 JSON（anchor 条目带尾部说明文字，取最后
    一个 } 之前的完整 JSON 对象）。"""
    form = FIXTURE_FORMS[step_id]
    return json.loads(form[: form.rindex("}") + 1])


def _fixture_evidence_dir(tmp_path: Path, name: str = "fixture") -> Path:
    """按手册 FIXTURE_FORMS 构造 12 步（不含审批）的隔离演练证据目录。"""
    directory = tmp_path / name
    directory.mkdir()
    for spec in _NON_APPROVAL_STEPS:
        _write_json(directory, spec.evidence_file, _fixture_payload(spec.step_id))
    return directory


def _approval_from_draft(draft: dict) -> dict:
    """以底稿哈希 + 人工字段组装审批记录（模拟审批人逐项确认后的手工组装）。"""
    return {
        "step": "cutover-approval",
        "schema_version": 1,
        "approved_at": "2026-09-05T10:00:00+00:00",
        "note": "隔离 fixture 演练审批（非生产验收）",
        "window": {
            "start": "2026-09-05T12:00:00+00:00",
            "end": "2026-09-05T15:00:00+00:00",
        },
        "rollback_plan": "fixture 回滚计划",
        "observation": "fixture 观察 1 小时",
        "approved_by": "fixture-approver",
        "step_evidence": dict(draft["step_evidence"]),
        "supporting_evidence": dict(draft["supporting_evidence"]),
    }


def _statuses(report: dict) -> dict[str, str]:
    return {item["step"]: item["status"] for item in report["steps"]}


def _cli(action: str, *, target_dir=None, evidence_dir=None, output=None) -> int:
    return cli_module._run_cutover_evidence_pack(
        SimpleNamespace(
            action=action,
            target_dir=str(target_dir) if target_dir else None,
            evidence_dir=str(evidence_dir) if evidence_dir else None,
            output=str(output) if output else None,
        )
    )


# --- 1. 13 步无漏项：模板与手册同源全覆盖 ----------------------------------------


def test_pack_files_are_13_templates_plus_companion_plus_readme() -> None:
    """产物恰为 13 步模板 + 锚副本模板 + README，无多无漏、字节确定性。"""
    files = build_pack_files()
    expected = {template_name(spec.evidence_file) for spec in STEPS}
    expected |= {template_name(ANCHOR_COMPANION_FILE), README_FILE}
    assert set(files) == expected
    assert len(files) == 15
    # 无时间戳等不确定内容：两次构建逐字节一致（幂等重放可比对的基础）
    assert build_pack_files() == files


def test_manual_template_fixture_registry_match_steps_exactly() -> None:
    """MANUAL_ENTRIES / TEMPLATE_FIELDS / FIXTURE_FORMS 与 STEPS 一一对应。"""
    step_ids = {spec.step_id for spec in STEPS}
    assert len(step_ids) == 13
    assert set(MANUAL_ENTRIES) == step_ids
    assert set(TEMPLATE_FIELDS) == step_ids
    assert set(FIXTURE_FORMS) == step_ids


def test_manual_entries_have_source_redaction_semantics_authorization() -> None:
    """13 步手册逐项四要素非空（来源/脱敏/语义/人工授权）。"""
    for step_id, entry in MANUAL_ENTRIES.items():
        for field in ("source", "redaction", "semantics", "authorization"):
            assert entry[field].strip(), f"{step_id} 缺 {field}"


def test_readme_covers_all_13_steps_with_six_elements_per_step() -> None:
    """README 逐步含 step_id、证据文件名、模板名与四要素 + fixture 形态；
    并含审批 SHA-256 计算、隔离 fixture 全链路、生产验收与人工授权声明。"""
    readme = render_pack_readme()
    assert "DRAFT/REPLACE-ME" in readme
    for spec in STEPS:
        assert spec.step_id in readme, f"README 缺步骤 {spec.step_id}"
        assert spec.evidence_file in readme, f"README 缺证据文件名 {spec.evidence_file}"
        assert template_name(spec.evidence_file) in readme
        entry = MANUAL_ENTRIES[spec.step_id]
        assert entry["source"] in readme
        assert entry["redaction"] in readme
        assert entry["semantics"] in readme
        assert entry["authorization"] in readme
        assert FIXTURE_FORMS[spec.step_id].split("（")[0] in readme
    assert "SHA-256" in readme and "approval-draft" in readme
    assert "隔离 fixture 全链路" in readme
    assert "不代表生产验收" in readme
    assert "逐项" in readme and "人工" in readme


def test_step_template_self_declares_step_with_placeholder_values() -> None:
    """模板 step 自声明正确（可读性），其余字段一律 REPLACE-ME 字符串。"""
    for spec in STEPS:
        template = build_step_template(spec.step_id)
        assert template["step"] == spec.step_id
        assert "_template_notice" in template and "DRAFT" in template["_template_notice"]
        for key, value in template.items():
            if key in ("step", "_template_notice", "_target_file"):
                continue
            if isinstance(value, str):
                assert value.startswith("REPLACE-ME"), (
                    f"{spec.step_id}.{key} 必须是 REPLACE-ME 形态"
                )

# --- 2. 模板防误用：不会被 rehearsal 误读，改名直用必 blocked ----------------------


def test_template_names_never_collide_with_rehearsal_evidence_files() -> None:
    """模板命名（.template）与 rehearsal 只认的精确证据文件名零相交。"""
    template_names = {template_name(spec.evidence_file) for spec in STEPS}
    template_names |= {template_name(ANCHOR_COMPANION_FILE)}
    assert template_names.isdisjoint(KNOWN_EVIDENCE_FILES)


def test_rehearsal_on_scaffold_dir_is_not_ready(tmp_path) -> None:
    """对脚手架目录直接跑 rehearsal：13 步全 not_executed、NOT READY；
    模板只出现在 unrecognized_files（不可能被当作证据评估）。"""
    target = _artifacts_pack_dir(tmp_path)
    scaffold_pack(target)
    report = run_cutover_rehearsal(target)
    statuses = _statuses(report)
    assert set(statuses.values()) == {"not_executed"}
    assert report["rehearsal_ready"] is False
    assert report["exit_code"] == 1
    unrecognized = {item["file"] for item in report["unrecognized_files"]}
    assert unrecognized == set(build_pack_files())


def test_renamed_templates_are_all_blocked_not_ready(tmp_path) -> None:
    """最恶劣误用：把 13 个模板改名为真实证据文件名——全部 blocked（
    REPLACE-ME 类型/枚举故意不符），整体 blocked、绝无 ready 读法。"""
    target = _artifacts_pack_dir(tmp_path)
    scaffold_pack(target)
    misuse = tmp_path / "misuse"
    misuse.mkdir()
    for spec in STEPS:
        source = target / template_name(spec.evidence_file)
        (misuse / spec.evidence_file).write_bytes(source.read_bytes())
    (misuse / ANCHOR_COMPANION_FILE).write_bytes(
        (target / template_name(ANCHOR_COMPANION_FILE)).read_bytes()
    )
    report = run_cutover_rehearsal(misuse)
    statuses = _statuses(report)
    assert set(statuses.values()) == {"blocked"}, statuses
    assert report["rehearsal_ready"] is False
    assert report["overall_status"] == "blocked"
    # 锚副本模板改名后：load_anchor_file 校验必然失败（缺锚字段）
    anchor_step = next(
        item for item in report["steps"] if item["step"] == "audit-chain-anchor"
    )
    assert anchor_step["status"] == "blocked"


def test_no_template_value_satisfies_pass_shape_by_construction() -> None:
    """逐字段核对：模板预填值对评估器要求的类型/枚举全部不满足（结构性
    防线，防止未来有人「顺手」把占位值改成合法形态）。"""
    checks = {
        "ci-main": lambda t: isinstance(t["run_id"], str),
        "release-check": lambda t: isinstance(t["all_green"], str),
        "search-smoke": lambda t: isinstance(t["executed"], str),
        "cloud-voice-smoke": lambda t: isinstance(t["executed"], str),
        "llm-smoke": lambda t: isinstance(t["executed"], str),
        "legacy-papers": lambda t: isinstance(t["pending_count"], str),
        "draft-ownership": lambda t: isinstance(t["pending_count"], str),
        "preflight-pre-migration": lambda t: isinstance(
            t["summary"]["pass"], str
        ),
        "backup-restore": lambda t: t["schema_version"] != "aios-backup-v1",
        "preflight-post-migration": lambda t: isinstance(
            t["summary"]["pending"], str
        ),
        "audit-chain-verify": lambda t: isinstance(t["valid"], str),
        "audit-chain-anchor": lambda t: t["anchor"]["status"]
        not in ("up-to-date", "valid", "invalid"),
        "cutover-approval": lambda t: isinstance(t["schema_version"], str),
    }
    for spec in STEPS:
        template = build_step_template(spec.step_id)
        assert checks[spec.step_id](template), (
            f"{spec.step_id} 模板存在可导致 pass 的合法形态预填值"
        )

# --- 3. 路径安全与幂等/拒绝覆盖 ---------------------------------------------------


def test_scaffold_creates_in_artifacts_guardrail_dir(tmp_path) -> None:
    """合法目标：artifacts/<新目录> 不存在时创建并写入全部产物。"""
    target = _artifacts_pack_dir(tmp_path)
    report = scaffold_pack(target)
    assert report["idempotent"] is False
    assert sorted(report["files_written"]) == sorted(build_pack_files())
    on_disk = {path.name: path.read_bytes() for path in target.iterdir()}
    assert on_disk == build_pack_files()


def test_scaffold_writes_into_existing_empty_dir(tmp_path) -> None:
    """已存在的空目录允许写入（仍不覆盖任何既有文件）。"""
    target = _artifacts_pack_dir(tmp_path)
    target.mkdir(parents=True)
    report = scaffold_pack(target)
    assert report["idempotent"] is False
    assert len(report["files_written"]) == 15


def test_scaffold_rejects_paths_outside_artifacts_temp(tmp_path) -> None:
    """护栏外（无 artifacts/temp 父目录的任意路径）拒绝且不留目录。"""
    with pytest.raises(PackInputError):
        scaffold_pack(tmp_path / "pack")
    with pytest.raises(PackInputError):
        scaffold_pack(tmp_path / "nested" / "deeper" / "pack")
    assert not (tmp_path / "pack").exists(), "拒绝时不得留下任何目录"


def test_scaffold_rejects_dotdot_escape(tmp_path) -> None:
    """`..` 越界：归一后落在护栏外即拒绝。"""
    sneaky = tmp_path / "artifacts" / ".." / "escape" / "pack"
    with pytest.raises(PackInputError):
        scaffold_pack(sneaky)
    assert not (tmp_path / "escape").exists()


def test_scaffold_rejects_artifacts_or_temp_dir_itself(tmp_path) -> None:
    """目标不能是 artifacts/ 或 temp/ 本身（要求独占新目录）。"""
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "temp").mkdir()
    with pytest.raises(PackInputError):
        scaffold_pack(tmp_path / "artifacts")
    with pytest.raises(PackInputError):
        scaffold_pack(tmp_path / "temp")


def test_scaffold_rejects_existing_file_target(tmp_path) -> None:
    """目标已被普通文件占用：拒绝且不改写该文件。"""
    occupied = _artifacts_pack_dir(tmp_path)
    occupied.parent.mkdir(parents=True)
    occupied.write_text("occupied", encoding="utf-8")
    with pytest.raises(PackInputError):
        scaffold_pack(occupied)
    assert occupied.read_text(encoding="utf-8") == "occupied"


def test_scaffold_rejects_symlink_target_and_ancestors(tmp_path) -> None:
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
    with pytest.raises(PackInputError):
        scaffold_pack(link_target)  # 目标本身是 symlink
    with pytest.raises(PackInputError):
        # 祖先组件是 symlink：即便链接目标恰在护栏内也不放行（不猜目标）
        scaffold_pack(tmp_path / "outside-link" / "pack")


def test_scaffold_idempotent_rerun_rewrites_nothing(tmp_path) -> None:
    """幂等重放：同目标再跑——字节一致 => 成功且零改写（mtime 不变）。"""
    target = _artifacts_pack_dir(tmp_path)
    scaffold_pack(target)
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    mtime_before = {
        path.name: path.stat().st_mtime_ns for path in target.iterdir()
    }
    report = scaffold_pack(target)
    assert report["idempotent"] is True
    assert report["files_written"] == []
    assert report["files_present"] == sorted(before)
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before
    mtime_after = {
        path.name: path.stat().st_mtime_ns for path in target.iterdir()
    }
    assert mtime_after == mtime_before, "幂等重放不得触碰任何文件"


def test_scaffold_rejects_conflicting_dir_and_keeps_bytes(tmp_path) -> None:
    """非空且非本工具脚手架（多余文件 / 模板被改 / 子目录占用模板名）一律
    拒绝覆盖；复原后回到幂等成功。"""
    target = _artifacts_pack_dir(tmp_path)
    scaffold_pack(target)
    before = {path.name: path.read_bytes() for path in target.iterdir()}
    # 多余文件
    (target / "operator-notes.txt").write_text("keep me", encoding="utf-8")
    with pytest.raises(PackInputError):
        scaffold_pack(target)
    # 模板被填写（字节变化，疑似操作者已开工——宁可拒绝不可覆盖）
    (target / "operator-notes.txt").unlink()
    victim = target / "ci-main.template.json"
    filled = victim.read_bytes().replace(b"REPLACE-ME: ", b"FIXED: ")
    victim.write_bytes(filled)
    with pytest.raises(PackInputError):
        scaffold_pack(target)
    assert victim.read_bytes() == filled, "拒绝时不得改写既有内容"
    victim.write_bytes(before["ci-main.template.json"])
    # 子目录占用模板名
    (target / "README.md").unlink()
    (target / "README.md").mkdir()
    with pytest.raises(PackInputError):
        scaffold_pack(target)
    (target / "README.md").rmdir()
    # 复原后回到幂等成功
    (target / "README.md").write_bytes(before["README.md"])
    report = scaffold_pack(target)
    assert report["idempotent"] is True
    assert {path.name: path.read_bytes() for path in target.iterdir()} == before


def test_resolve_scaffold_target_returns_resolved_guarded_path(tmp_path) -> None:
    """护栏通过的路径返回 resolve 归一结果；护栏外直接抛错。"""
    resolved = resolve_scaffold_target(_artifacts_pack_dir(tmp_path))
    assert resolved == _artifacts_pack_dir(tmp_path).resolve()
    with pytest.raises(PackInputError):
        resolve_scaffold_target(tmp_path / "elsewhere" / "pack")

# --- 4. 零敏感：模板/手册/底稿无敏感键、无凭据字样 -------------------------------


def test_templates_have_no_sensitive_keys_or_embedded_credentials() -> None:
    """13 步模板 + 锚副本模板：解析后无敏感键、无 ://user:pass@ 内嵌凭据。"""
    for spec in STEPS:
        template = build_step_template(spec.step_id)
        assert find_sensitive_key(template) is None, spec.step_id
        assert find_embedded_credential(template) is None, spec.step_id
    companion = json.loads(
        cep.build_anchor_companion_template().decode("utf-8").strip()
    )
    assert find_sensitive_key(companion) is None
    assert find_embedded_credential(companion) is None


def test_pack_files_contain_no_credential_url_pattern() -> None:
    """全部脚手架产物字节（模板 + 手册）不含凭据 URL 形态字样。"""
    for name, payload in build_pack_files().items():
        text = payload.decode("utf-8")
        assert not _CREDENTIAL_URL_RE.search(text), f"{name} 含凭据 URL 形态"
        assert PASSWORD_MARKER not in text and API_KEY_MARKER not in text


def test_approval_draft_never_echoes_sensitive_values(tmp_path) -> None:
    """底稿对敏感证据只报字段路径不回显值；marker 在输出 JSON 零泄漏。"""
    directory = tmp_path / "evidence"
    directory.mkdir()
    for spec in _NON_APPROVAL_STEPS:
        payload = _fixture_payload(spec.step_id)
        if spec.step_id == "search-smoke":
            payload["api_key"] = API_KEY_MARKER  # 有意违规：敏感键进证据
        if spec.step_id == "backup-restore":
            payload["created_at"] = CREDENTIAL_URL_MARKER  # 有意违规：凭据 URL
        _write_json(directory, spec.evidence_file, payload)
    draft = build_approval_draft(directory)
    dumped = json.dumps(draft, ensure_ascii=False)
    assert API_KEY_MARKER not in dumped
    assert PASSWORD_MARKER not in dumped
    assert not _CREDENTIAL_URL_RE.search(dumped)
    assert draft["load_problems"]["search-smoke.json"] == (
        "证据含敏感键 api_key（rehearsal 将判 blocked；值不回显）"
    )
    assert "backup-restore.json" in draft["load_problems"]
    # 哈希仍精确：违规文件也被哈希（rehearsal 自会判 blocked，底稿不拦判定）
    raw = (directory / "search-smoke.json").read_bytes()
    assert draft["step_evidence"]["search-smoke"] == hashlib.sha256(raw).hexdigest()


# --- 5. approval-draft：哈希精确性 / 失效语义 / 不可直接当作审批 ------------------


def test_approval_draft_hashes_match_file_bytes_exactly(tmp_path) -> None:
    """底稿 step_evidence/supporting_evidence 与文件字节 sha256 精确一致。"""
    directory = _fixture_evidence_dir(tmp_path)
    (directory / ANCHOR_COMPANION_FILE).write_bytes(b"anchor-bytes\n")
    draft = build_approval_draft(directory)
    for spec in _NON_APPROVAL_STEPS:
        raw = (directory / spec.evidence_file).read_bytes()
        assert draft["step_evidence"][spec.step_id] == (
            hashlib.sha256(raw).hexdigest()
        )
    assert draft["supporting_evidence"][ANCHOR_COMPANION_FILE] == (
        hashlib.sha256(b"anchor-bytes\n").hexdigest()
    )
    assert draft["step_evidence_missing"] == []
    assert draft["approval_file_present"] is False
    assert draft["draft"] is True
    assert set(draft["manual_fields_required"]) == set(MANUAL_APPROVAL_FIELDS)


def test_approval_draft_lists_missing_steps_honestly(tmp_path) -> None:
    """缺文件如实列 missing；cutover-approval.json 自身不进绑定/缺失清单。"""
    directory = tmp_path / "partial"
    directory.mkdir()
    _write_json(
        directory, "ci-main.json", _fixture_payload("ci-main")
    )
    draft = build_approval_draft(directory)
    assert set(draft["step_evidence"]) == {"ci-main"}
    assert set(draft["step_evidence_missing"]) == {
        spec.step_id for spec in _NON_APPROVAL_STEPS if spec.step_id != "ci-main"
    }
    assert "cutover-approval" not in draft["step_evidence_missing"]
    # 脚手架目录本身：13 个证据文件全缺（模板不算证据）
    target = _artifacts_pack_dir(tmp_path)
    scaffold_pack(target)
    draft = build_approval_draft(target)
    assert draft["step_evidence"] == {}
    assert set(draft["step_evidence_missing"]) == {
        spec.step_id for spec in _NON_APPROVAL_STEPS
    }


def test_approval_draft_flags_existing_approval_file(tmp_path) -> None:
    """审批文件已存在时 approval_file_present 如实标注（仍不哈希自身）。"""
    directory = _fixture_evidence_dir(tmp_path)
    draft = build_approval_draft(directory)
    _write_json(directory, "cutover-approval.json", _approval_from_draft(draft))
    redraft = build_approval_draft(directory)
    assert redraft["approval_file_present"] is True
    assert "cutover-approval" not in redraft["step_evidence"]
    assert redraft["step_evidence"] == draft["step_evidence"]


def test_stale_draft_hashes_fail_rehearsal_after_evidence_change(
    tmp_path,
) -> None:
    """证据变更后底稿失效：以旧底稿哈希构建的审批 => rehearsal blocked
    （哈希失配）；以新底稿重建 => 恢复 pass（哈希精确性的时序面）。"""
    directory = _fixture_evidence_dir(tmp_path)
    stale = build_approval_draft(directory)
    # 审批前证据被改动（如复跑 preflight 重新导出）
    changed = _fixture_payload("preflight-pre-migration")
    changed["summary"]["pending"] = 3
    _write_json(directory, "preflight-pre-migration.json", changed)
    _write_json(directory, "cutover-approval.json", _approval_from_draft(stale))
    report = run_cutover_rehearsal(directory)
    approval = next(
        item for item in report["steps"] if item["step"] == "cutover-approval"
    )
    assert approval["status"] == "blocked"
    assert approval["data"]["hash_mismatches"] == ["preflight-pre-migration"]
    assert report["rehearsal_ready"] is False
    # 新底稿重新绑定后恢复
    fresh = build_approval_draft(directory)
    assert (
        fresh["step_evidence"]["preflight-pre-migration"]
        != stale["step_evidence"]["preflight-pre-migration"]
    )
    _write_json(directory, "cutover-approval.json", _approval_from_draft(fresh))
    report = run_cutover_rehearsal(directory)
    assert report["rehearsal_ready"] is True


def test_draft_misused_as_approval_is_blocked_not_ready(tmp_path) -> None:
    """底稿字节直接改名为 cutover-approval.json => malformed => blocked，
    不会被误判 ready（缺 step/必填审批字段是结构性防线）。"""
    directory = _fixture_evidence_dir(tmp_path)
    draft = build_approval_draft(directory)
    (directory / "cutover-approval.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8"
    )
    report = run_cutover_rehearsal(directory)
    approval = next(
        item for item in report["steps"] if item["step"] == "cutover-approval"
    )
    assert approval["status"] == "blocked"
    assert report["rehearsal_ready"] is False
    assert report["overall_status"] == "blocked"


def _approval_from_draft_with_metadata(draft: dict) -> dict:
    """最恶劣误用形态（M10-16 返工场景）：在底稿上补齐 step 与全部合法
    人工审批字段、哈希照抄底稿——但原样保留全部 draft 专用元数据字段。"""
    assembled = _approval_from_draft(draft)
    for key in APPROVAL_DRAFT_RESERVED_FIELDS:
        assembled[key] = draft[key]
    return assembled


def test_filled_draft_with_draft_metadata_kept_is_blocked_not_ready(
    tmp_path,
) -> None:
    """DRAFT 不可审批边界（M10-16 返工回归）：底稿补齐 step 与全部合法
    审批字段、哈希精确匹配，只要保留 draft 专用元数据字段 => cutover-approval
    blocked 且整体 not ready——评估器 fail-closed 拒绝保留元数据字段，
    「补齐后改名」不再有 pass 读法；移除全部 draft 专用元数据（只保留合法
    审批字段 + 底稿哈希）=> pass / rehearsal_ready=True。"""
    directory = _fixture_evidence_dir(tmp_path)
    draft = build_approval_draft(directory)
    # 阶段一：补齐全部合法字段但保留全部 draft 专用元数据 => blocked
    _write_json(
        directory, "cutover-approval.json", _approval_from_draft_with_metadata(draft)
    )
    report = run_cutover_rehearsal(directory)
    approval = next(
        item for item in report["steps"] if item["step"] == "cutover-approval"
    )
    assert approval["status"] == "blocked"
    assert report["rehearsal_ready"] is False
    assert report["overall_status"] == "blocked"
    # reason 透出命中的保留字段名（可审计；全部 10 个字段都带上了）
    for field in APPROVAL_DRAFT_RESERVED_FIELDS:
        assert field in approval["reason"], field
    # 其余 12 步不受影响：只有审批步 blocked
    assert all(
        item["status"] == "pass"
        for item in report["steps"]
        if item["step"] != "cutover-approval"
    )
    # 阶段二：移除全部 draft 专用元数据（合法组装形态）=> pass / ready
    _write_json(directory, "cutover-approval.json", _approval_from_draft(draft))
    report = run_cutover_rehearsal(directory)
    approval = next(
        item for item in report["steps"] if item["step"] == "cutover-approval"
    )
    assert approval["status"] == "pass"
    assert report["rehearsal_ready"] is True
    assert report["overall_status"] == "pass"
    assert report["exit_code"] == 0


def test_single_draft_metadata_field_alone_is_blocked(tmp_path) -> None:
    """只保留一个 draft 专用元数据字段（其余全部移除、字段合法齐备）
    => 仍 blocked：字段存在即拒绝，与值/其余字段无关。"""
    directory = _fixture_evidence_dir(tmp_path)
    draft = build_approval_draft(directory)
    payload = _approval_from_draft(draft)
    payload["draft"] = True  # 最容易「顺手」留下的一个
    _write_json(directory, "cutover-approval.json", payload)
    report = run_cutover_rehearsal(directory)
    approval = next(
        item for item in report["steps"] if item["step"] == "cutover-approval"
    )
    assert approval["status"] == "blocked"
    assert "draft" in approval["reason"]
    assert report["rehearsal_ready"] is False


def test_reserved_draft_fields_cover_all_draft_metadata(tmp_path) -> None:
    """守卫：底稿输出的全部元数据字段（除审批合法共享的 step_evidence/
    supporting_evidence 外）都必须登记在 rehearsal 的拒绝名单内——底稿
    新增元数据字段而漏登记 APPROVAL_DRAFT_RESERVED_FIELDS 会直接红。"""
    directory = _fixture_evidence_dir(tmp_path)
    draft = build_approval_draft(directory)
    draft_only = set(draft) - {"step_evidence", "supporting_evidence"}
    assert draft_only == set(APPROVAL_DRAFT_RESERVED_FIELDS)


# --- 6. 文档化隔离 fixture 全链路：FIXTURE_FORMS => rehearsal_ready ---------------


def test_fixture_forms_are_parseable_and_self_declare_step() -> None:
    """12 步（非审批）fixture 形态可解析、自声明 step 与文件名对应步一致。"""
    for spec in _NON_APPROVAL_STEPS:
        payload = _fixture_payload(spec.step_id)
        assert payload["step"] == spec.step_id


def test_documented_fixture_full_chain_reaches_ready(tmp_path) -> None:
    """README 记载的隔离 fixture 全链路真实可复现：手册 FIXTURE_FORMS 构造
    12 步 + approval-draft 底稿哈希 + 人工字段 => rehearsal_ready=True。
    fixture 值不代表任何真实执行结果——ready 仅是演练时间线证据齐备。"""
    directory = _fixture_evidence_dir(tmp_path, name="cutover-fixture")
    draft = build_approval_draft(directory)
    _write_json(directory, "cutover-approval.json", _approval_from_draft(draft))
    report = run_cutover_rehearsal(directory)
    statuses = _statuses(report)
    assert set(statuses.values()) == {"pass"}
    assert report["rehearsal_ready"] is True
    assert report["exit_code"] == 0
    # 演练值是 fixture：manifest 明确隔离声明，不构成生产验收
    assert "不代表生产验收" in report["isolation_note"]


# --- 7. CLI 注册与行为面 -----------------------------------------------------------


def test_cli_registered_and_has_no_execute_flag(tmp_path, monkeypatch) -> None:
    """CLI 子命令注册、无 --yes 执行形态（argparse 对未知旗标 exit 2）。"""
    assert hasattr(cli_module, "_run_cutover_evidence_pack")
    source = Path(cli_module.__file__).read_text(encoding="utf-8")
    assert '"cutover-evidence-pack"' in source
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "cutover-evidence-pack",
            "scaffold",
            "--target-dir",
            str(_artifacts_pack_dir(tmp_path)),
            "--yes",
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 2


def test_main_dispatch_scaffold_and_approval_draft(
    tmp_path, monkeypatch, capsys
) -> None:
    """main 分发：scaffold 建包 exit 0；approval-draft 打印 DRAFT 底稿 exit 0。"""
    target = _artifacts_pack_dir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["cli", "cutover-evidence-pack", "scaffold", "--target-dir", str(target)],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "脚手架已创建" in out and "不代表生产验收" in out
    directory = _fixture_evidence_dir(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cli",
            "cutover-evidence-pack",
            "approval-draft",
            "--evidence-dir",
            str(directory),
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert '"draft": true' in out
    assert "DRAFT：以上是哈希底稿" in out


def test_cli_missing_required_args_exit_2(tmp_path, capsys) -> None:
    """scaffold 缺 --target-dir / approval-draft 缺 --evidence-dir => exit 2。"""
    assert _cli("scaffold") == 2
    assert _cli("approval-draft") == 2
    assert _cli("scaffold", target_dir=tmp_path / "nowhere") == 2


def test_cli_scaffold_guardrail_exit_2_without_side_effects(
    tmp_path, capsys
) -> None:
    """CLI 层护栏：护栏外目标 exit 2，且打印未创建说明。"""
    assert _cli("scaffold", target_dir=tmp_path / "pack") == 2
    assert "拒绝写入" in capsys.readouterr().out
    assert not (tmp_path / "pack").exists()


def test_cli_approval_draft_output_guardrail_and_atomic_write(
    tmp_path, capsys
) -> None:
    """--output 越界拒绝 exit 2；artifacts 内允许（原子写），文件与打印一致。"""
    directory = _fixture_evidence_dir(tmp_path)
    assert (
        _cli("approval-draft", evidence_dir=directory, output=tmp_path / "out.json")
        == 2
    )
    assert "拒绝写入" in capsys.readouterr().out
    assert not (tmp_path / "out.json").exists()
    guarded = tmp_path / "artifacts" / "cutover-approval.DRAFT.json"
    assert (
        _cli("approval-draft", evidence_dir=directory, output=guarded) == 0
    )
    capsys.readouterr()
    written = json.loads(guarded.read_text(encoding="utf-8"))
    assert written["draft"] is True
    assert written["step_evidence"] == build_approval_draft(directory)[
        "step_evidence"
    ]


def test_cli_approval_draft_bad_evidence_dir_exit_2(tmp_path, capsys) -> None:
    """证据目录不存在 => EvidenceInputError => exit 2。"""
    assert _cli("approval-draft", evidence_dir=tmp_path / "missing") == 2
    assert "证据输入无效" in capsys.readouterr().out


def test_module_has_no_env_db_or_network_surface() -> None:
    """行为面：AST 级零 os.environ / 零 DB 引擎 / 零 HTTP/套接字引用
    （只看真实代码，文档字符串与注释里的说明性字样不计）。"""
    import ast

    tree = ast.parse(Path(cep.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {(node.module or "").split(".")[0]}
        else:
            names = set()
        assert names.isdisjoint(
            {"httpx", "socket", "requests", "urllib", "asyncpg", "psycopg",
             "sqlalchemy", "psycopg2", "aiohttp"}
        ), f"模块不得导入 {names}"
        if isinstance(node, ast.Attribute) and node.attr == "environ":
            raise AssertionError("模块不得访问环境变量（os.environ）")
        if isinstance(node, ast.Name) and node.id == "create_engine":
            raise AssertionError("模块不得创建数据库引擎")


def test_rehearsal_shared_layer_regression_untouched(tmp_path) -> None:
    """回归锚：共享 evidence_kit 层行为不变（空目录 rehearsal 全 not_executed、
    exit 1——与 M10-15 语义一致，脚手架改动不触碰装载层）。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    report = run_cutover_rehearsal(empty)
    assert set(_statuses(report).values()) == {"not_executed"}
    assert report["rehearsal_ready"] is False
    assert report["exit_code"] == 1
