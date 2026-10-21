"""M15-024 学习模式：草稿↔练习卷反向关联 + 存量回填。

Revision ID: 0032_draft_materialized_paper
Revises: 0031_llm_provider_profiles
Create Date: 2026-10-21

语义：
- course_generation_drafts.materialized_paper_id：materialize 产出的
  练习卷 id——学习视图（/papers/{id}/study）由此反查课程 plan
  （章节目标/解释/逐章练习），支撑「先学习再考试」。
- 存量回填：按 owner + 标题前缀「定制课程 · {goal[:40]}」匹配。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0032_draft_materialized_paper"
down_revision = "0031_llm_provider_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "course_generation_drafts",
        sa.Column("materialized_paper_id", sa.String(64), nullable=True, index=True),
    )
    # 存量回填（标题前缀匹配；同 goal 多次 materialize 取最新卷）
    # UPDATE...FROM 仅 PG 语法——SQLite 测试库跳过（测试内 materialize 会回写，
    # 无存量需回填；生产 PG 执行完整回填）
    from alembic import context as _ctx
    _dialect = _ctx.get_context().dialect.name if _ctx.is_offline_mode() is False else ""
    if _dialect == "postgresql" or _dialect == "":
        op.execute(
            """
            UPDATE course_generation_drafts d
            SET materialized_paper_id = sub.pid
            FROM (
                SELECT d2.id AS did, p.id AS pid,
                       ROW_NUMBER() OVER (PARTITION BY d2.id ORDER BY p.id DESC) AS rn
                FROM course_generation_drafts d2
                JOIN papers p ON p.owner_id = d2.owner_id
                            AND p.title = '定制课程 · ' || left(d2.goal, 40)
                WHERE d2.materialized_paper_id IS NULL
            ) sub
            WHERE d.id = sub.did AND sub.rn = 1
            """
        )


def downgrade() -> None:
    # SQLite drop column 不级联索引——先删索引再删列
    op.drop_index("ix_course_generation_drafts_materialized_paper_id", table_name="course_generation_drafts")
    op.drop_column("course_generation_drafts", "materialized_paper_id")
