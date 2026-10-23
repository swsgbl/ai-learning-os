"""M18-001 课程资源目录域层（审计升级包 Prompt 1 / Phase 1）。

设计边界（审计包 02 §4.1 / 07 Prompt 1 硬性约束）：
- **目录模式（Mode A）默认**：只同步元数据与外链，不下载 blob——
  ChinaTextbook 无根 LICENSE/逐教材授权清单，policy_default=
  metadata_only（审计包 02 §2/§3：未知许可不默认共享原文）；
- 同步可重复且幂等：pin 快照 commit SHA；同 SHA 重跑 = unchanged 增量；
- **不依赖 README 固定行号**：以 git tree（结构化）为目录事实源，
  路径段归一化为元数据（学段/学科/版本/年级/册别）；
- 保留原始路径（source_path 不变形）；分片识别只认 allowlist 后缀
  （.pdf.N）且按**数值**排序（审计包 02 §4.2）；
- 跨来源去重靠 content hash（本期 blob 未下载——留给 Phase 2），
  期内以 (provider, source_path) 为自然键稳定引用。

纯函数、无 I/O：网络获取（tree/README）由调用方注入 fetcher（可打桩）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 学段词表（路径归一化；新学段词经人工确认后加入）
EDUCATION_STAGES: tuple[str, ...] = ("小学", "初中", "高中", "大学")
#: 「五·四学制」等变体目录的规整名
_STAGE_ALIASES: dict[str, str] = {
    "小学（五·四学制）": "小学",
    "初中（五·四学制）": "初中",
}

#: 学科词表（常见教材学科；超出词表不猜——subject 留空由元数据补充）
SUBJECT_HINTS: tuple[str, ...] = (
    "数学", "语文", "英语", "物理", "化学", "生物", "历史", "地理",
    "政治", "道德与法治", "科学", "信息科技", "高等数学", "线性代数",
    "离散数学", "概率论", "概率论与数理统计",
)

#: 分片后缀（allowlist——审计包 02 §4.2：只认显式声明的形态）
# 尾锚定即可（前缀任意——search 语义匹配 .pdf.N 结尾的文件名）
_PART_PATTERN = re.compile(r"\.pdf\.(\d+)$")

#: 同步 run 计数器字段（审计包 02 §4.1 §5）
SYNC_COUNTERS: tuple[str, ...] = (
    "discovered", "created", "updated", "unchanged", "unavailable",
    "policy_blocked", "failed",
)


class CatalogSyncError(ValueError):
    """非法目录输入（未知 provider/空树/路径异常）。"""


@dataclass(frozen=True, slots=True)
class PartInfo:
    """PDF 分片清单条目（.pdf.N——数值序）。"""

    part_index: int
    source_path: str


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """归一化后的目录条目（tree blob → 元数据；不含内容）。"""

    source_path: str
    title: str
    education_stage: str = ""
    subject: str = ""
    grade: str = ""
    semester: str = ""
    volume: str = ""
    edition_hint: str = ""
    mime_type: str = ""
    is_part: bool = False
    parts: tuple[PartInfo, ...] = field(default_factory=tuple)

    @property
    def logical_path(self) -> str:
        """分片组的逻辑文件（去 .N 后缀——组级引用键）。"""
        if self.is_part:
            return re.sub(r"\.pdf\.\d+$", ".pdf", self.source_path)
        return self.source_path


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """一次目录同步的结果计数（审计包 02 §4.1 §5）。"""

    provider: str
    commit_sha: str
    entries: list[CatalogEntry]
    counters: dict[str, int]

    def counter_line(self) -> str:
        return " ".join(f"{k}={self.counters.get(k, 0)}" for k in SYNC_COUNTERS)


def normalize_path(raw_path: str, size_bytes: int | None = None) -> CatalogEntry | None:
    """tree 文件路径 → 归一化目录条目。

    规则：
    - 只收 PDF（含分片）；目录节点/其他类型返回 None（不计入 discovered）；
    - 路径段解析：学段（含别名规整）→学科（词表命中）→年级/册别启发；
      **不猜**——未命中词表的段保留在 title/edition_hint，不虚构学科；
    - 分片（.pdf.N）：is_part=True 并挂到组（组=同目录同名去 .N）。
    """
    path = raw_path.strip("/")
    if not path or path.endswith("/"):
        return None
    if not (path.lower().endswith(".pdf") or _PART_PATTERN.search(path)):
        return None
    segments = [seg for seg in path.split("/") if seg]
    filename = segments[-1] if segments else ""
    if not filename:
        return None
    dirs = segments[:-1]

    stage = ""
    subject = ""
    grade = ""
    edition_hint = ""
    for seg in dirs:
        normalized = _STAGE_ALIASES.get(seg, seg)
        if not stage and normalized in EDUCATION_STAGES:
            stage = normalized
            continue
        if not subject:
            # 最长命中优先（「高等数学」先于「数学」——子串关系词表顺序不可依赖）
            hit = max((s for s in SUBJECT_HINTS if s in seg), key=len, default="")
            if hit:
                subject = hit
                if seg != hit:
                    edition_hint = edition_hint or seg
                continue
        # 年级启发（七年级/高一/大一/第X册等）——仅当段短且含关键词
        if not grade and re.search(r"[一二三四五六七八九]年级|高三|高[一二]|[大][一二三四]|年级", seg):
            grade = seg
            continue
        if seg not in (stage, subject) and len(seg) <= 24:
            edition_hint = edition_hint or seg

    is_part = bool(_PART_PATTERN.search(filename))
    title = _PART_PATTERN.sub("", filename)
    # 册别（上册/下册/必修N/选修N/第N册）从文件名提取
    volume = ""
    volume_hit = re.search(r"(上册|下册|必修[一二三\d]+|选择性必修[一二三\d]+|选修[一二三\d]+|第[一二三四五六\d]+册)", title)
    if volume_hit:
        volume = volume_hit.group(1)
    semester_hit = re.search(r"(上学期|下学期|春季|秋季|第一学期|第二学期)", title)
    semester = semester_hit.group(1) if semester_hit else ""

    return CatalogEntry(
        source_path=path,
        title=title or filename,
        education_stage=stage,
        subject=subject,
        grade=grade,
        semester=semester,
        volume=volume,
        edition_hint=edition_hint,
        mime_type="application/pdf",
        is_part=is_part,
    )


def group_parts(entries: list[CatalogEntry]) -> list[CatalogEntry]:
    """分片归组：同 logical_path 的 .pdf.N 条目合并为组条目（数值序 parts）。

    组条目（is_part=False）携带 parts 清单；孤立的普通 PDF 保持原样。
    组内缺失序号（1..max 不连续）如实保留缺口——完整性校验留给导入
    阶段（Phase 2 拉取时验片），目录层不虚构「完整」。
    """
    groups: dict[str, list[CatalogEntry]] = {}
    standalone: list[CatalogEntry] = []
    for entry in entries:
        if entry.is_part:
            groups.setdefault(entry.logical_path, []).append(entry)
        else:
            standalone.append(entry)
    merged: list[CatalogEntry] = []
    standalone_by_path = {e.source_path: e for e in standalone}
    merged.extend(e for e in standalone if e.source_path not in groups)
    for logical, parts in groups.items():
        ordered = sorted(
            parts,
            key=lambda e: int(_PART_PATTERN.search(e.source_path).group(1)),  # type: ignore[union-attr]
        )
        base = standalone_by_path.get(logical, ordered[0])
        merged.append(
            CatalogEntry(
                source_path=logical,
                title=base.title,
                education_stage=base.education_stage,
                subject=base.subject,
                grade=base.grade,
                semester=base.semester,
                volume=base.volume,
                edition_hint=base.edition_hint,
                mime_type=base.mime_type,
                is_part=True,
                parts=tuple(
                    PartInfo(
                        part_index=int(_PART_PATTERN.search(p.source_path).group(1)),  # type: ignore[union-attr]
                        source_path=p.source_path,
                    )
                    for p in ordered
                ),
            )
        )
    return merged


def summarize_sync(
    provider: str,
    commit_sha: str,
    tree_paths: list[tuple[str, str, int | None]],
) -> SyncOutcome:
    """tree blob 清单 → 同步结果（幂等差量由调用方结合库内现状计算）。

    tree_paths: [(path, blob_id, size)]；size 缺省 None（tree API 可不带）。
    unavailable/created/updated/unchanged 的差量语义在 API 层（比对库内
    同 source_path 的最新 commit_sha/blob_id）；本函数负责归一化+分组+
    discovered 计数。
    """
    if not commit_sha:
        raise CatalogSyncError("commit_sha 为空——同步必须 pin 快照（不跟随浮动分支）")
    entries: list[CatalogEntry] = []
    discovered = 0
    for path, _blob_id, _size in tree_paths:
        entry = normalize_path(path)
        if entry is None:
            continue
        discovered += 1
        entries.append(entry)
    grouped = group_parts(entries)
    counters = {key: 0 for key in SYNC_COUNTERS}
    counters["discovered"] = discovered
    return SyncOutcome(
        provider=provider,
        commit_sha=commit_sha,
        entries=grouped,
        counters=counters,
    )
