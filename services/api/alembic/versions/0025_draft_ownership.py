"""draft-ownership: M9-05 四类草稿归属（course_import / paper_question 可回填；
course_generation / variant_question 无资源锚保持 NULL）。

Revision ID: 0025_draft_ownership
Revises: 0024_admin_audit
Create Date: 2026-09-02
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0025_draft_ownership"
down_revision = "0024_admin_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in (
        "course_import_drafts",
        "paper_question_drafts",
        "course_generation_drafts",
        "variant_question_drafts",
    ):
        op.add_column(table, sa.Column("owner_id", sa.String(length=32), nullable=True))
        op.create_index(f"ix_{table}_owner_id", table, ["owner_id"])

    # 资源关联型草稿经 resources.owner_id 回填（不可靠者保持 NULL）
    op.execute(
        "UPDATE course_import_drafts SET owner_id = ("
        "  SELECT r.owner_id FROM resources r"
        "  WHERE r.id = course_import_drafts.source_resource_id)"
    )
    op.execute(
        "UPDATE paper_question_drafts SET owner_id = ("
        "  SELECT r.owner_id FROM resources r"
        "  WHERE r.id = paper_question_drafts.resource_id)"
    )


def downgrade() -> None:
    for table in (
        "variant_question_drafts",
        "course_generation_drafts",
        "paper_question_drafts",
        "course_import_drafts",
    ):
        op.drop_index(f"ix_{table}_owner_id", table_name=table)
        op.drop_column(table, "owner_id")
