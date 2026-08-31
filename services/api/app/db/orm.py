"""ORM models mirroring docs/delivery/04 数据模型与 API 契约.

约束对齐契约 §6 关键数据库约束：
- answer_events (exam_session_id, sequence) 唯一且单调；
- submissions.exam_session_id 唯一（重复提交幂等的数据库兜底）。
字段保持双方言（PostgreSQL / SQLite 测试替身）：不使用 JSONB、原生 UUID 等 PG 专属类型。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PaperRow(Base):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    subtitle: Mapped[str] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(256))
    university: Mapped[str | None] = mapped_column(String(128))
    year: Mapped[int | None] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(64))
    difficulty: Mapped[str] = mapped_column(String(32))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    origin_url: Mapped[str | None] = mapped_column(String(1024))
    license: Mapped[str] = mapped_column(String(64))


class QuestionRow(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"), index=True)
    question_type: Mapped[str] = mapped_column(String(32))
    stem: Mapped[str] = mapped_column(String(4096))
    options: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # answer/explanation/angles 仅存服务端，任何 active 响应不得下发。
    answer: Mapped[str] = mapped_column(String(1024))
    explanation: Mapped[str] = mapped_column(String(4096))
    angles: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    knowledge: Mapped[list[Any]] = mapped_column(JSON, default=list)
    score: Mapped[float] = mapped_column(Float, default=1.0)
    difficulty: Mapped[int] = mapped_column(Integer, default=3)
    sort_order: Mapped[int] = mapped_column(Integer)


class ExamSessionRow(Base):
    __tablename__ = "exam_sessions"

    exam_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(ForeignKey("papers.id"), index=True)
    paper_title: Mapped[str] = mapped_column(String(256))
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnswerEventRow(Base):
    __tablename__ = "answer_events"
    __table_args__ = (
        UniqueConstraint("exam_session_id", "sequence", name="uq_answer_events_exam_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_session_id: Mapped[str] = mapped_column(
        ForeignKey("exam_sessions.exam_id"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    question_id: Mapped[str] = mapped_column(String(64))
    answer: Mapped[str] = mapped_column(String(1024))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubmissionRow(Base):
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exam_session_id: Mapped[str] = mapped_column(
        ForeignKey("exam_sessions.exam_id"), unique=True, index=True
    )
    paper_id: Mapped[str] = mapped_column(String(64))
    paper_title: Mapped[str] = mapped_column(String(256))
    mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    score: Mapped[int] = mapped_column(Integer)
    correct_count: Mapped[int] = mapped_column(Integer)
    total_count: Mapped[int] = mapped_column(Integer)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    # items 是已定稿的评分明细快照（含 expected/explanation），交卷后才可下发。
    items: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 判分规则版本，保证评分结果可追溯（docs/delivery M2-08 验收）。
    rule_version: Mapped[str] = mapped_column(String(32), default="objective-v1")


__all__ = [
    "AnswerEventRow",
    "ChunkRow",
    "EvidenceRow",
    "ExamSessionRow",
    "MisconceptionCandidateRow",
    "PaperRow",
    "ParseJobRow",
    "QuestionRow",
    "ResourceRow",
    "StudentConceptStateRow",
    "SubmissionRow",
    "VoiceSessionRow",
    "VoiceTranscriptRow",
]


class SourceRow(Base):
    """M1-01 Source Registry：S/A/B/C/U 分层 + robots + rate limit + license state。"""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), unique=True)
    source_type: Mapped[str] = mapped_column(String(32))
    authority_score: Mapped[int] = mapped_column(Integer, default=50)
    homepage: Mapped[str] = mapped_column(String(1024))
    terms_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    robots_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rate_limit: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trust_tier: Mapped[str] = mapped_column(String(4), default="U")
    license_state: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class ResourceRow(Base):
    """M1-03 上传资源：SHA-256 内容寻址去重 + license 快照（04 号文档 §2.4）。"""

    __tablename__ = "resources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("sources.id"), nullable=True
    )
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    media_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(512))
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    access_state: Mapped[str] = mapped_column(String(16), default="unknown")
    license_state: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    storage_key: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parse_status: Mapped[str] = mapped_column(String(32), default="pending")
    parser_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    parse_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChunkRow(Base):
    """M1-06 chunk：可回到页码/slide 的文本片段。"""

    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("resources.id"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    chunk_hash: Mapped[str] = mapped_column(String(64))
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slide: Mapped[int | None] = mapped_column(Integer, nullable=True)
    block_types: Mapped[list[Any]] = mapped_column(JSON, default=list)
    embedding_status: Mapped[str] = mapped_column(String(32), default="pending")


class EvidenceRow(Base):
    """M1-06 Evidence（04 号文档 §2.15）：parser、hash、locator、license 快照。"""

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chunks.id"), index=True
    )
    resource_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("resources.id"), index=True
    )
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    parser_name: Mapped[str] = mapped_column(String(64))
    locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    snippet_hash: Mapped[str] = mapped_column(String(64))
    license_state: Mapped[str] = mapped_column(String(32))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ParseJobRow(Base):
    """M1-07 解析任务：幂等键 (resource_id, parser_name)；可重试可恢复。"""

    __tablename__ = "parse_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    resource_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("resources.id"), index=True
    )
    parser_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("resource_id", "parser_name", name="uq_parse_jobs_resource_parser"),
    )


class ConceptRow(Base):
    """M3-02 概念本体（04 号文档 §2.3）：canonical 数据，跨版本 upsert 不删除。"""

    __tablename__ = "concepts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(256))
    aliases: Mapped[list[Any]] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(2048), default="")
    difficulty: Mapped[int] = mapped_column(Integer, default=3)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConceptDagVersionRow(Base):
    """M3-02 概念图版本：每次发布生成不可变新版本，历史可回溯。"""

    __tablename__ = "concept_dag_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(512), default="")
    # 完整节点快照：概念本体可演进（upsert），历史版本回读必须看到当时的属性
    nodes: Mapped[list[Any]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("version", name="uq_concept_dag_versions_version"),)


class ConceptEdgeRow(Base):
    """M3-02 先修关系边（04 号文档 §2.3 concept_edges）：随版本存储，先修 -> 概念。"""

    __tablename__ = "concept_edges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("concept_dag_versions.id"), index=True
    )
    prerequisite_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("concepts.id"), index=True
    )
    concept_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("concepts.id"), index=True
    )

    __table_args__ = (
        UniqueConstraint("version_id", "prerequisite_id", "concept_id", name="uq_concept_edges_triple"),
    )


class StudentConceptStateRow(Base):
    """M3-03 学生-概念状态：从学习事件全量重算的物化状态（事件源不变，可随时整体重建）。

    MVP 单用户无 user_id 列；认证引入后再加复合主键 (user_id, concept_id)。
    """

    __tablename__ = "student_concept_states"

    concept_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mastery: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    forgetting_risk: Mapped[float] = mapped_column(Float)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, default=0)
    wrong_count: Mapped[int] = mapped_column(Integer, default=0)
    first_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MisconceptionCandidateRow(Base):
    """M3-04 误解候选：(concept_id, pattern) 唯一，从学习事件全量重算的物化状态。

    单次错误即 candidate（低置信度），独立题数达到阈值才 confirmed（长期画像）。
    """

    __tablename__ = "misconception_candidates"

    concept_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pattern: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    independent_count: Mapped[int] = mapped_column(Integer, default=0)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    question_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VoiceTranscriptRow(Base):
    """M4-02 语音转写落盘：transcript 恒存（runbook「只保留转写、意图和必要事件」）；
    原始音频仅 PRIVACY_STORE_AUDIO=true 时写对象存储，否则显式丢弃并留 audio_stored=false。"""

    __tablename__ = "voice_transcripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    audio_bytes: Mapped[int] = mapped_column(Integer, default=0)
    audio_object_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    audio_stored: Mapped[bool] = mapped_column(Boolean, default=False)
    exam_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # M4-03 FSM 绑定
    question_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VoiceSessionRow(Base):
    """M4-03 语音会话 FSM 持久化：status 由 voice_session_fsm 迁移矩阵校验。

    revision 乐观并发计数：每次事件应用 +1，客户端带 revision 可检测并发冲突。
    question_index 为当前交互题在试卷题目列表中的 0-based 序号。
    """

    __tablename__ = "voice_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    exam_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32))
    question_index: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
