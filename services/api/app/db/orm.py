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
    CHAR,
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    false as sa_false,
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
    # M10-03: 试卷归属；NULL = 系统公共卷（seed 卷与 auth off 期间导入的卷）
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


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
    # M9-02: 考试归属；NULL = 认证关闭的本地调试模式
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


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
    # M9-02: dedup 作用域 = (owner_id, content_hash)——跨用户同内容各自保留
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    parse_status: Mapped[str] = mapped_column(String(32), default="pending")
    parser_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_metrics: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    parse_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("owner_id", "content_hash", name="uq_resources_owner_content"),)


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
    # M9-02: 转写回查面归属；NULL = 本地调试模式
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
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


class VoiceAnswerEventRow(Base):
    """M4-05 规范化语音答案事件：event_id 为客户端幂等键（唯一约束兜底重放）。

    accepted=false 记录含糊/无效答案的澄清路径（不含答案，只留审计痕迹）；
    accepted=true 的 answer 已由服务端经 exam answer_events 确定性提交。
    """

    __tablename__ = "voice_answer_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    exam_id: Mapped[str] = mapped_column(String(64))
    question_id: Mapped[str] = mapped_column(String(64))
    normalized_answer: Mapped[str | None] = mapped_column(String(256), nullable=True)
    intent: Mapped[str] = mapped_column(String(32))
    transcript: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VoiceTraceSpanRow(Base):
    """M4-09 语音链路耗时观测 span：source 区分 server 自动埋点 / client 上报。

    观测只记录不判定：trace 数据不影响任何业务状态；
    vad/llm/first_audio 发生在客户端与上游模型，由客户端上报（source=client），
    服务端只自动埋 asr/tts/intent/fsm 四个可测环节——不虚报测不到的阶段。
    """

    __tablename__ = "voice_trace_spans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stage: Mapped[str] = mapped_column(String(32), index=True)
    duration_ms: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16))  # server | client
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exam_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SearchQueryRow(Base):
    """M5-01 搜索执行记录：查询计划、结果与弃用原因可记录（backlog M5-01）。

    providers_skipped 含 per-provider 弃用原因（未配置/未知名称等）；
    results 存结果摘要 JSON；一次执行一行，append-only。
    """

    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(String(512))
    providers_requested: Mapped[list[Any]] = mapped_column(JSON, default=list)
    providers_skipped: Mapped[list[Any]] = mapped_column(JSON, default=list)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    results: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # M9-04: 搜索记录归属发起用户（泄漏修复——原全局可读）
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLogRow(Base):
    """M9-04 治理审计：license 变更 / DAG 发布 / 草稿审核 / 角色提升留痕。"""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(128))
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    request_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AuditChainEntryRow(Base):
    """M10-04 治理审计哈希链 entry：与 audit_log 一一对应（audit_id PK）。

    entry_hash = sha256(canonical(previous_hash + sequence + audit 稳定字段))；
    FK ON DELETE RESTRICT 让「删审计行不留痕」在数据库层即被拒绝。
    """

    __tablename__ = "audit_chain_entries"

    audit_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("audit_log.id", ondelete="RESTRICT"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("sequence", name="uq_audit_chain_entries_sequence"),
        UniqueConstraint("entry_hash", name="uq_audit_chain_entries_entry_hash"),
    )


class AuditChainStateRow(Base):
    """M10-04 哈希链单行状态：append 的事务级锁点（FOR UPDATE 串行分配 sequence）。

    CHECK id=1 保证全库至多一行；last_sequence/last_hash 指向链尾。
    """

    __tablename__ = "audit_chain_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    last_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (CheckConstraint("id = 1", name="ck_audit_chain_state_singleton"),)


class CourseImportDraftRow(Base):
    """M5-05 课程导入草稿：授权资源 -> Course/Concept/Resource 草稿 -> 人工审核。

    授权门禁在生成时判定（reuse_admission 快照落库可审计）；concepts 为
    确定性规则提取的候选列表；approved/rejected 为终态。
    """

    __tablename__ = "course_import_drafts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(24), default="pending_review", index=True)
    source_resource_id: Mapped[str] = mapped_column(String(64))
    source_license_state: Mapped[str] = mapped_column(String(32))
    reuse_admission: Mapped[str] = mapped_column(String(32))
    concepts: Mapped[list[Any]] = mapped_column(JSON, default=list)
    resource_refs: Mapped[list[Any]] = mapped_column(JSON, default=list)
    extraction_note: Mapped[str] = mapped_column(String(256))
    review_note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PaperQuestionDraftRow(Base):
    """M5-06 试卷题目抽取草稿：题目 JSON + 页码 + 审核终态（不虚报，未见即无）。"""

    __tablename__ = "paper_question_drafts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    resource_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending_review", index=True)
    questions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    question_count: Mapped[int] = mapped_column()
    extraction_note: Mapped[str] = mapped_column(String(256))
    review_note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CourseGenerationDraftRow(Base):
    """M5-07 课程生成工作流草稿：八阶段产物 JSON + DAG 版本快照 + 审核终态。"""

    __tablename__ = "course_generation_drafts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    goal: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(24), default="pending_review", index=True)
    dag_version: Mapped[int] = mapped_column()
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    chapter_count: Mapped[int] = mapped_column()
    generation_note: Mapped[str] = mapped_column(String(512))
    # M15-024：materialize 产出的练习卷（学习视图反查课程 plan 的锚点）
    materialized_paper_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    review_note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))



class VariantQuestionDraftRow(Base):
    """M5-08 变式题生成草稿：解保持变换产出 + 概念映射/原题 evidence 保留。"""

    __tablename__ = "variant_question_drafts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending_review", index=True)
    variants: Mapped[dict] = mapped_column(JSON)
    variant_count: Mapped[int] = mapped_column(Integer)
    generation_note: Mapped[str] = mapped_column(String(512))
    review_note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))



class EvalRunRow(Base):
    """M6-01 评测运行记录：golden set agreement report 落库可回查。"""

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    rule_version: Mapped[str] = mapped_column(String(64))
    case_count: Mapped[int] = mapped_column(Integer)
    agreement: Mapped[float | None] = mapped_column(nullable=True)
    report: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AiProviderSecretRow(Base):
    """M15-008 provider 密钥治理：槽位唯一，密文经 AUTH_SECRET 派生密钥
    AES-256-GCM 加密（app.core.secrets.SecretBox）；last4 是密钥尾 4 位
    脱敏展示（供治理页状态显示），明文密钥绝不落库。"""

    __tablename__ = "ai_provider_secrets"

    slot: Mapped[str] = mapped_column(String(32), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(String(8))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserAiKeyRow(Base):
    """M15-014 个人 AI 密钥（BYOK）：学习者自带的 LLM/云 ASR/TTS 密钥。

    与 AiProviderSecretRow（全局部署槽位）同款加密语义（SecretBox
    AES-256-GCM，明文不落库），差异仅在归属维度：user_id + slot 联合
    主键，请求装配时个人密钥优先于全局槽位。"""

    __tablename__ = "user_ai_keys"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    slot: Mapped[str] = mapped_column(String(32), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(String(8))
    # M15-015: 个人 LLM 可选路由（非机密明文；空 = 沿用服务端全局配置）
    endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[str] = mapped_column(DateTime(timezone=True))


class UserLlmProfileRow(Base):
    """M15-018 多厂商 LLM 密钥库：每用户多 profile，恰一条激活。

    密钥密文（SecretBox）+ 用户命名 label + 可选 endpoint/model；
    判分取 active profile；无 profile = 全局配置（语义不变）。
    """

    __tablename__ = "user_llm_profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    label: Mapped[str] = mapped_column(String(64))
    ciphertext: Mapped[str] = mapped_column(Text)
    last4: Mapped[str] = mapped_column(String(8))
    endpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=sa_false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserRow(Base):
    """M9-01 用户：本地用户名密码账户。密码只存 bcrypt 哈希。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    # M9-04: 角色——默认 learner；admin 仅经安全运维 CLI 提升（无默认管理员账号）
    role: Mapped[str] = mapped_column(String(16), default="learner", server_default="learner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- M18-001 课程目录（审计升级包 Phase 1）


class ResourceProviderRow(Base):
    """目录来源方（Mode A：仅元数据与外链——不下载 blob）。"""

    __tablename__ = "resource_providers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    base_url: Mapped[str] = mapped_column(String(255))
    provider_type: Mapped[str] = mapped_column(String(32), default="git_tree")
    sync_adapter: Mapped[str] = mapped_column(String(32), default="github_tree")
    #: 审计 02 §3：未知许可默认 metadata_only（不默认共享原文）
    policy_default: Mapped[str] = mapped_column(String(32), default="metadata_only")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResourceRecordRow(Base):
    """目录条目（tree blob 归一化元数据；source_path 保真不变形）。"""

    __tablename__ = "resource_records"
    __table_args__ = (
        UniqueConstraint("provider_id", "source_path", name="uq_resource_provider_path"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(32), ForeignKey("resource_providers.id"), index=True)
    source_path: Mapped[str] = mapped_column(String(512))
    #: 外链（raw/blob URL——Mode A 打开来源，不镜像内容）
    source_url: Mapped[str] = mapped_column(String(1024), default="")
    title: Mapped[str] = mapped_column(String(255))
    title_normalized: Mapped[str] = mapped_column(String(255), default="")
    education_stage: Mapped[str] = mapped_column(String(32), default="")
    subject: Mapped[str] = mapped_column(String(64), default="")
    grade: Mapped[str] = mapped_column(String(32), default="")
    semester: Mapped[str] = mapped_column(String(32), default="")
    volume: Mapped[str] = mapped_column(String(32), default="")
    edition_hint: Mapped[str] = mapped_column(String(64), default="")
    mime_type: Mapped[str] = mapped_column(String(64), default="")
    #: 快照锚点（审计 02 §4.1：pin commit，不跟随浮动分支）
    source_commit_sha: Mapped[str] = mapped_column(String(40), default="")
    #: 分片清单 JSON（[{part_index, source_path}]；空 = 完整单文件）
    parts_json: Mapped[list] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CatalogSyncRunRow(Base):
    """目录同步运行（幂等差量计数——审计 02 §4.1 §5）。"""

    __tablename__ = "catalog_sync_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(32), ForeignKey("resource_providers.id"), index=True)
    commit_sha: Mapped[str] = mapped_column(String(40))
    #: running / succeeded / failed
    status: Mapped[str] = mapped_column(String(16), default="running")
    counters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ContentPermissionRow(Base):
    """资源许可状态（审计 02 §3：六态——禁止布尔 is_open）。"""

    __tablename__ = "content_permissions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(32), ForeignKey("resource_records.id"), unique=True, index=True)
    #: unknown / metadata_only / user_private / approved_shared / review_required / blocked
    status: Mapped[str] = mapped_column(String(32), default="metadata_only")
    license_spdx: Mapped[str] = mapped_column(String(64), default="")
    license_uri: Mapped[str] = mapped_column(String(255), default="")
    attribution_text: Mapped[str] = mapped_column(Text, default="")
    review_evidence_uri: Mapped[str] = mapped_column(String(255), default="")
    reviewed_by: Mapped[str] = mapped_column(String(64), default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- M18-003 私有导入（审计升级包 Phase 2）


class IngestJobRow(Base):
    """导入任务（状态机见 domain/ingest——终态不复活，重试=新任务）。

    (owner_id, resource_id) 唯一——同用户同资源重复导入幂等返回既有。
    """

    __tablename__ = "ingest_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "resource_id", name="uq_ingest_owner_resource"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    resource_id: Mapped[str] = mapped_column(String(32), ForeignKey("resource_records.id"))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    parts_total: Mapped[int] = mapped_column(Integer, default=0)
    parts_fetched: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    assembled_object_key: Mapped[str] = mapped_column(String(255), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ContentBlobRow(Base):
    """私有内容对象（user_private——授权逐请求校验，不依赖 key 结构）。"""

    __tablename__ = "content_blobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(32), index=True)
    resource_id: Mapped[str] = mapped_column(String(32), ForeignKey("resource_records.id"))
    job_id: Mapped[str] = mapped_column(String(32), ForeignKey("ingest_jobs.id"))
    owner_scope: Mapped[str] = mapped_column(String(24), default="user_private")
    object_key: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    mime_type: Mapped[str] = mapped_column(String(64), default="application/pdf")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- M18-005 课程结构（审计升级包 Phase 4）


class CourseUnitRow(Base):
    """章节树单元（候选→确认两态——出版 outline 候选，人工确认后正式）。

    挂 resources（解析产物），锚点保留 toc 页码；同一 resource 重提取
    = 整树替换（sequence 重建）。
    """

    __tablename__ = "course_units"
    __table_args__ = (
        UniqueConstraint("resource_id", "sequence", name="uq_course_unit_resource_seq"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(64), ForeignKey("resources.id"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(32), ForeignKey("course_units.id"), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(255))
    #: volume / chapter / section（按 toc 相对层级——确认时可人工校正）
    unit_type: Mapped[str] = mapped_column(String(16), default="chapter")
    level: Mapped[int] = mapped_column(Integer, default=1)
    #: candidate（生成态）/ confirmed（人工确认）
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    #: 页锚点与原始 toc level（{page, toc_level}）
    anchor: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- M18-006 概念候选（审计升级包 Phase 4 后半）


class ConceptCandidateRow(Base):
    """概念节点候选（LLM 提取——确认后经既有 DAG 发布管道进正式图）。"""

    __tablename__ = "concept_candidates"
    __table_args__ = (
        UniqueConstraint("resource_id", "concept_id", name="uq_concept_cand_resource_concept"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(64), ForeignKey("resources.id"), index=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    concept_id: Mapped[str] = mapped_column(String(64))
    canonical_name: Mapped[str] = mapped_column(String(256))
    subject: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(String(2048), default="")
    difficulty: Mapped[int] = mapped_column(Integer, default=3)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    #: candidate / confirmed / rejected
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConceptEdgeCandidateRow(Base):
    """概念边候选（typed relation——正式 DAG 本期只消费 prerequisite_of）。"""

    __tablename__ = "concept_edge_candidates"
    __table_args__ = (
        UniqueConstraint(
            "resource_id", "from_concept", "to_concept", "relation",
            name="uq_concept_edge_cand_natural",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(64), ForeignKey("resources.id"), index=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    from_concept: Mapped[str] = mapped_column(String(64))
    to_concept: Mapped[str] = mapped_column(String(64))
    relation: Mapped[str] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- M18-007 题目生命周期（审计 Prompt 6）


class QuestionLifecycleRow(Base):
    """题目发布门（审计 04 §4.3 练习/考试分离 + Prompt 6 红线）。

    - 生成题入池即 candidate——练习（practice/review/transfer）可用；
    - 等值前后测蓝图只收 published——人工 publish 后才进正式考试；
    - **无 lifecycle 行的历史题按 published 语义**（legacy 兼容——
      公共 seed 卷与既有蓝图不破坏；门只对新生成题生效）。
    """

    __tablename__ = "question_lifecycle"
    __table_args__ = (
        UniqueConstraint("question_id", name="uq_question_lifecycle_question"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    #: candidate / published / retired
    status: Mapped[str] = mapped_column(String(16), default="candidate")
    #: 验证方式记录（answer_key=客观题键校验；manual=人工复核）
    verify_method: Mapped[str] = mapped_column(String(32), default="")
    reviewed_by: Mapped[str] = mapped_column(String(64), default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LearningContractRow(Base):
    """M16-001 学习合同：可观察能力目标 + 范围/排除项 + 先验 + 通过标准。

    升级方案 Phase 1——目标不是主题名，是 3-8 个「给定 X，能在 Z 标准
    下完成 Y」的可观察结果（target_outcomes JSON）；确认由用户显式
    （confirmed_at），LLM 不可代确认。
    """

    __tablename__ = "learning_contracts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(64), default="", server_default="")
    # exam_sprint / intro / project / research 四种模式（方案 Phase 4）
    mode: Mapped[str] = mapped_column(String(32), default="exam_sprint", server_default="exam_sprint")
    target_outcomes: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    scope_included: Mapped[list[str]] = mapped_column(JSON)
    scope_excluded: Mapped[list[str]] = mapped_column(JSON)
    prerequisites: Mapped[list[str]] = mapped_column(JSON)
    time_budget_hours: Mapped[float] = mapped_column(Float)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pass_criteria: Mapped[str] = mapped_column(Text)
    # Phase 2 接入（前测/等值后测蓝图）；无蓝图 = 评估走既有题库
    assessment_blueprint_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LearningSprintRow(Base):
    """M16-001 学习 Sprint：48h 编排状态机（phase 主线 + paused 独立标志）。

    只有领域服务（sprint_state + 路由服务层）可推进；plan_version 在
    REPLANNABLE 阶段修改范围时递增，不覆盖旧计划。
    """

    __tablename__ = "learning_sprints"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    contract_id: Mapped[str] = mapped_column(String(32), ForeignKey("learning_contracts.id"), index=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    phase: Mapped[str] = mapped_column(String(32), default="created", server_default="created")
    paused: Mapped[bool] = mapped_column(Boolean, server_default=sa_false())
    pause_reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    plan_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    baseline_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    posttest_result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LearningEventRow(Base):
    """M16-001 学习事件：幂等键去重的审计事实流（不可静默覆盖）。

    idempotency_key 全局唯一（重复提交返回既有事件，不重复落库）；
    source 区分 api/llm/voice/system；privacy_class 供日志脱敏分层。
    """

    __tablename__ = "learning_events"
    __table_args__ = (
        UniqueConstraint("idem_scope_key", name="uq_learning_events_idem"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sprint_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    contract_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # M16-001b：租户命名空间幂等键（"{owner}#{用户键}"）——唯一约束在此
    idem_scope_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="api", server_default="api")
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    privacy_class: Mapped[str] = mapped_column(String(16), default="normal", server_default="normal")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LearningAttemptRow(Base):
    """M16-002 作答证据（升级方案 Phase 2）：每次作答的完整学习证据。

    三态判分（correct 可空=未判分）；信心/耗时/提示/独立/延迟/迁移 +
    错误类型标签 + 作答上下文。ConceptMastery 不落表——由
    domain/mastery.derive_mastery 从本表原始证据实时重算。
    """

    __tablename__ = "learning_attempts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    sprint_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    contract_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    item_id: Mapped[str] = mapped_column(String(64))
    concept_id: Mapped[str] = mapped_column(String(64), index=True)
    response: Mapped[str] = mapped_column(Text, default="", server_default="")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hints_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    independent: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    delayed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    transfer: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    error_tags: Mapped[list[str]] = mapped_column(JSON)
    context: Mapped[str] = mapped_column(String(64), default="practice", server_default="practice")
    grading_method: Mapped[str] = mapped_column(String(32), default="manual", server_default="manual")
    grader_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssessmentBlueprintRow(Base):
    """M16-004 等值前后测蓝图：每概念 pretest/posttest 分配（不重叠）。

    由 domain/assessment_blueprint.build_blueprint 构建（纯函数），此处
    持久化；complete=False 如实记录覆盖缺口。
    """

    __tablename__ = "assessment_blueprints"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    contract_id: Mapped[str] = mapped_column(String(32), index=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    concept_id: Mapped[str] = mapped_column(String(64))
    pretest_item_ids: Mapped[list[str]] = mapped_column(JSON)
    posttest_item_ids: Mapped[list[str]] = mapped_column(JSON)
    complete: Mapped[bool] = mapped_column(Boolean, server_default=sa_false())
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LearningReviewRow(Base):
    """M16-005 间隔复习队列：每概念一条活跃记录（scheduled→…→completed）。

    复习完成只能来自真实作答（attempt_id 关联判分作答）；间隔由
    domain/review_schedule 计算（FSRS adapter 接入前的 MVP 调度器）。
    """

    __tablename__ = "learning_reviews"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    sprint_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    contract_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    concept_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="scheduled", server_default="scheduled")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    algorithm_version: Mapped[str] = mapped_column(
        String(32), default="intervals-mvp-1", server_default="intervals-mvp-1"
    )
    #: M17-003 FSRS 记忆状态（Card.to_dict——stability/difficulty 跨轮
    #: 演化）；模板调度器（intervals-mvp）恒 NULL
    memory_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))



class LearningSourceRefRow(Base):
    """M16-008 来源证据（升级方案 Phase 5）：合同概念的检索片段。

    url/title/snippet/content_hash（可追溯）；license_hint 默认 unknown
    （许可不明时如实标注——不虚构开放许可）。
    """

    __tablename__ = "learning_source_refs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    contract_id: Mapped[str] = mapped_column(String(32), index=True)
    concept_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(512), default="", server_default="")
    snippet: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    license_hint: Mapped[str] = mapped_column(String(64), default="unknown", server_default="unknown")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
