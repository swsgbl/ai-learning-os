"""M18-003 导入域层测试：分片连续性/组装校验/大小上限/状态机（审计 Prompt 3）。"""
from __future__ import annotations

import pytest

from app.domain.ingest import (
    DEFAULT_MAX_TOTAL_BYTES,
    IngestError,
    PartManifest,
    assemble_pdf,
    enforce_size_limit,
    ingest_status_transition,
    sha256_hex,
)

_PDF = b"%PDF-1.7 fake body"


def _parts() -> list[bytes]:
    return [b"%PDF-1.7 ", b"part2 ", b"part3"]


def test_manifest_continuity_ok() -> None:
    manifest = PartManifest(entries=((1, "a.pdf.1"), (2, "a.pdf.2"), (3, "a.pdf.3")))
    manifest.validate_continuity()  # 不抛


def test_manifest_missing_part_rejected() -> None:
    manifest = PartManifest(entries=((1, "a.pdf.1"), (3, "a.pdf.3")))
    with pytest.raises(IngestError, match="缺片 \\[2\\]"):
        manifest.validate_continuity()


def test_manifest_duplicate_part_rejected() -> None:
    manifest = PartManifest(entries=((1, "a.pdf.1"), (1, "a.pdf.1b"), (2, "a.pdf.2")))
    with pytest.raises(IngestError, match="重复片 \\[1\\]"):
        manifest.validate_continuity()


def test_manifest_must_start_at_one() -> None:
    manifest = PartManifest(entries=((2, "a.pdf.2"), (3, "a.pdf.3")))
    with pytest.raises(IngestError, match="从 1 起"):
        manifest.validate_continuity()


def test_assemble_produces_pdf_and_hash() -> None:
    parts = _parts()
    assembled, digest = assemble_pdf(parts)
    assert assembled == b"".join(parts)
    assert digest == sha256_hex(b"".join(parts))
    assert assembled.startswith(b"%PDF-")


def test_assemble_verifies_part_hashes() -> None:
    parts = _parts()
    hashes = [sha256_hex(p) for p in parts]
    _, digest = assemble_pdf(parts, part_hashes=hashes)
    assert digest
    with pytest.raises(IngestError, match="哈希不匹配"):
        assemble_pdf(parts, part_hashes=["0" * 64] + hashes[1:])


def test_assemble_rejects_non_pdf() -> None:
    with pytest.raises(IngestError, match="不是合法 PDF"):
        assemble_pdf([b"JUNK", b"DATA"])


def test_assemble_rejects_empty() -> None:
    with pytest.raises(IngestError, match="无分片"):
        assemble_pdf([])


def test_size_limit_enforced() -> None:
    enforce_size_limit(1024)
    with pytest.raises(IngestError, match="超上限"):
        enforce_size_limit(DEFAULT_MAX_TOTAL_BYTES + 1)


def test_status_machine_happy_path() -> None:
    assert ingest_status_transition("queued", "downloading") == "downloading"
    assert ingest_status_transition("downloading", "assembled") == "assembled"
    assert ingest_status_transition("assembled", "ready") == "ready"


def test_status_machine_terminal_states() -> None:
    for terminal in ("ready", "incomplete_parts", "failed"):
        with pytest.raises(IngestError, match="非法导入状态转换"):
            ingest_status_transition(terminal, "queued")


def test_status_machine_no_skip() -> None:
    with pytest.raises(IngestError, match="非法导入状态转换"):
        ingest_status_transition("queued", "assembled")  # 跳过 downloading
