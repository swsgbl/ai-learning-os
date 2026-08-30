"""initial schema: papers, questions, exam_sessions, answer_events, submissions

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-31

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "papers",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("subtitle", sa.String(length=512), nullable=False),
        sa.Column("source", sa.String(length=256), nullable=False),
        sa.Column("university", sa.String(length=128), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("subject", sa.String(length=64), nullable=False),
        sa.Column("difficulty", sa.String(length=32), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("origin_url", sa.String(length=1024), nullable=True),
        sa.Column("license", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_papers")),
    )
    op.create_table(
        "questions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("paper_id", sa.String(length=64), nullable=False),
        sa.Column("question_type", sa.String(length=32), nullable=False),
        sa.Column("stem", sa.String(length=4096), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("answer", sa.String(length=1024), nullable=False),
        sa.Column("explanation", sa.String(length=4096), nullable=False),
        sa.Column("angles", sa.JSON(), nullable=False),
        sa.Column("knowledge", sa.JSON(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["papers.id"], name=op.f("fk_questions_paper_id_papers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_questions")),
    )
    op.create_index(
        op.f("ix_questions_paper_id"), "questions", ["paper_id"], unique=False
    )
    op.create_table(
        "exam_sessions",
        sa.Column("exam_id", sa.String(length=64), nullable=False),
        sa.Column("paper_id", sa.String(length=64), nullable=False),
        sa.Column("paper_title", sa.String(length=256), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["paper_id"], ["papers.id"], name=op.f("fk_exam_sessions_paper_id_papers")
        ),
        sa.PrimaryKeyConstraint("exam_id", name=op.f("pk_exam_sessions")),
    )
    op.create_index(
        op.f("ix_exam_sessions_paper_id"), "exam_sessions", ["paper_id"], unique=False
    )
    op.create_table(
        "answer_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("exam_session_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.String(length=64), nullable=False),
        sa.Column("answer", sa.String(length=1024), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["exam_session_id"],
            ["exam_sessions.exam_id"],
            name=op.f("fk_answer_events_exam_session_id_exam_sessions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_events")),
        sa.UniqueConstraint(
            "exam_session_id", "sequence", name="uq_answer_events_exam_sequence"
        ),
    )
    op.create_index(
        op.f("ix_answer_events_exam_session_id"),
        "answer_events",
        ["exam_session_id"],
        unique=False,
    )
    op.create_table(
        "submissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("exam_session_id", sa.String(length=64), nullable=False),
        sa.Column("paper_id", sa.String(length=64), nullable=False),
        sa.Column("paper_title", sa.String(length=256), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("correct_count", sa.Integer(), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rule_version", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["exam_session_id"],
            ["exam_sessions.exam_id"],
            name=op.f("fk_submissions_exam_session_id_exam_sessions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_submissions")),
        sa.UniqueConstraint("exam_session_id", name=op.f("uq_submissions_exam_session_id")),
    )
    op.create_index(
        op.f("ix_submissions_exam_session_id"),
        "submissions",
        ["exam_session_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_submissions_exam_session_id"), table_name="submissions")
    op.drop_table("submissions")
    op.drop_index(op.f("ix_answer_events_exam_session_id"), table_name="answer_events")
    op.drop_table("answer_events")
    op.drop_index(op.f("ix_exam_sessions_paper_id"), table_name="exam_sessions")
    op.drop_table("exam_sessions")
    op.drop_index(op.f("ix_questions_paper_id"), table_name="questions")
    op.drop_table("questions")
    op.drop_table("papers")
