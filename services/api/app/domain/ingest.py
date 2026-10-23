"""M18-003 用户私有导入与 PDF 分片组装（审计升级包 Prompt 3 / Phase 2）。

设计边界（审计 02 §4.2 / 07 Prompt 3）：
- 分片只认 allowlist 后缀（.pdf.N），按**数值**序（非字典序）；
- 序号必须连续（1..N 无缺无重）——缺片/重复片 = incomplete（不进
  搜索/生成，如实标记）；
- 组装 = 二进制拼接（ChinaTextbook 的分片是字节段——确认来源格式后
  才拼；**绝不执行来源仓库里的 EXE**）；
- 拼接后校验 PDF 魔数（%PDF-）与基本结构——不可读 = parse_failed；
- SHA-256 逐片与整体双算（完整性可追溯）；
- 下载 URL 只来自服务端目录条目（source_url——不接受用户传 URL，
  SSRF 面收敛）；下载有大小上限与串行节流。

纯函数（校验/组装）；下载/存储由 API 层注入。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: 导入状态机（审计 03 §5 Import job 裁剪——MVP 无 OCR/解析段）
INGEST_STATUSES: tuple[str, ...] = (
    "queued",           # 已受理（policy 过、权利声明齐）
    "downloading",      # 分片拉取中
    "assembled",        # 拼接完成且校验通过
    "ready",            # 落库可引用
    "incomplete_parts",  # 缺片/重复片（如实——不虚构完整）
    "failed",           # 下载/拼接/校验失败
)

#: 单文件大小上限（字节；env 可覆写——硬顶防滥用）
DEFAULT_MAX_TOTAL_BYTES = 500 * 1024 * 1024

_PDF_MAGIC = b"%PDF-"


class IngestError(ValueError):
    """非法导入输入。"""


@dataclass(frozen=True, slots=True)
class PartManifest:
    """分片清单（数值序；来自目录条目 parts_json）。"""

    entries: tuple[tuple[int, str], ...]  # (part_index, source_path)

    @property
    def indexes(self) -> tuple[int, ...]:
        return tuple(i for i, _ in self.entries)

    def validate_continuity(self) -> None:
        """序号 1..N 连续无缺无重（从 1 起——审计 02 §4.2）。"""
        indexes = self.indexes
        if not indexes:
            raise IngestError("分片清单为空")
        if indexes[0] != 1:
            raise IngestError(f"分片序号必须从 1 起（首片={indexes[0]}）")
        expected = list(range(1, len(indexes) + 1))
        if list(indexes) != expected:
            missing = sorted(set(expected) - set(indexes))
            duplicated = sorted({i for i in indexes if indexes.count(i) > 1})
            detail = []
            if missing:
                detail.append(f"缺片 {missing}")
            if duplicated:
                detail.append(f"重复片 {duplicated}")
            raise IngestError("分片序号不连续：" + "；".join(detail))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def assemble_pdf(parts: list[bytes], *, part_hashes: list[str] | None = None) -> tuple[bytes, str]:
    """数值序分片 → (整体 PDF 字节, 整体 sha256)。

    - parts 必须已按 part_index 数值序排好（调用方保证）；
    - part_hashes 提供时逐一复核（不匹配 = IngestError，fail-closed）；
    - 拼接后校验 %PDF- 魔数（不可读 = IngestError）。
    """
    if not parts:
        raise IngestError("无分片可组装")
    if part_hashes is not None and len(part_hashes) != len(parts):
        raise IngestError("分片哈希清单与分片数不一致")
    if part_hashes is not None:
        for index, (chunk, expected) in enumerate(zip(parts, part_hashes), start=1):
            actual = sha256_hex(chunk)
            if actual != expected:
                raise IngestError(f"第 {index} 片哈希不匹配（期望 {expected[:12]}… 实得 {actual[:12]}…）")
    assembled = b"".join(parts)
    if not assembled.startswith(_PDF_MAGIC):
        raise IngestError("拼接结果不是合法 PDF（缺 %PDF- 魔数）——来源分片格式与预期不符")
    return assembled, sha256_hex(assembled)


def enforce_size_limit(total_bytes: int, limit: int = DEFAULT_MAX_TOTAL_BYTES) -> None:
    if total_bytes > limit:
        raise IngestError(f"总大小 {total_bytes} 超上限 {limit}——拒绝导入（可经部署配置调整）")


def ingest_status_transition(current: str, event: str) -> str:
    """导入状态机（显式转移——审计 03 §5：仅安全处可重试）。

    queued → downloading → assembled → ready
    任何进行态 → incomplete_parts / failed（终态——重试=新 job）
    """
    if current not in INGEST_STATUSES:
        raise IngestError(f"未知导入状态: {current!r}")
    legal = {
        "queued": {"downloading", "failed"},
        "downloading": {"assembled", "incomplete_parts", "failed"},
        "assembled": {"ready", "failed"},
        "ready": set(),
        "incomplete_parts": set(),
        "failed": set(),
    }
    if event not in legal[current]:
        raise IngestError(f"非法导入状态转换: {current!r} → {event!r}")
    return event
