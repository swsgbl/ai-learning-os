"""learning-contracts: M16-001 学习合同/Sprint 状态机/学习事件（升级方案 Phase 1）。

Revision ID: 0033_learning_contracts
Revises: 0032_draft_materialized_paper
Create Date: 2026-10-22

语义：
- learning_contracts：可观察能力目标（3-8）+ 范围/排除/先验/时间预算/
  通过标准 + 用户显式确认（confirmed_at）；
- learning_sprints：48h Sprint 状态机载体（phase 主线 + paused 独立 +
  plan_version 递增不覆盖旧计划；baseline/posttest 结果 JSON）；
- learning_events：幂等键去重的审计事件流（source/trace/privacy_class）。
- 纯新增表，不触碰既有数据；downgrade 整表删除（无存量依赖）。
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0033_learning_contracts"
down_revision = "0032_draft_materialized_paper"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "learning_contracts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner_id", sa.String(32), nullable=True, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("domain", sa.String(64), nullable=False, server_default=""),
        sa.Column("mode", sa.String(32), nullable=False, server_default="exam_sprint"),
        sa.Column("target_outcomes", sa.JSON(), nullable=False),
        sa.Column("scope_included", sa.JSON(), nullable=False),
        sa.Column("scope_excluded", sa.JSON(), nullable=False),
        sa.Column("prerequisites", sa.JSON(), nullable=False),
        sa.Column("time_budget_hours", sa.Float(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pass_criteria", sa.Text(), nullable=False),
        sa.Column("assessment_blueprint_id", sa.String(32), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "learning_sprints",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "contract_id",
            sa.String(32),
            sa.ForeignKey("learning_contracts.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("owner_id", sa.String(32), nullable=True, index=True),
        sa.Column("phase", sa.String(32), nullable=False, server_default="created"),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("pause_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline_result", sa.JSON(), nullable=True),
        sa.Column("posttest_result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "learning_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("sprint_id", sa.String(32), nullable=True, index=True),
        sa.Column("contract_id", sa.String(32), nullable=True, index=True),
        sa.Column("owner_id", sa.String(32), nullable=True, index=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        # M16-001b：幂等唯一作用域键 = "{owner_id}#{用户键}"——全局唯一约束
        # 挪到这里；idempotency_key 保留用户原值仅作展示。修复安全审查：
        # 跨租户同键 IDOR（B 用 A 的键拿到 A 的事件）与键预占压制
        # （B 先占键吞掉 A 的事件）。owner 可空（本地模式）用 anon 前缀。
        sa.Column("idem_scope_key", sa.String(160), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="api"),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("privacy_class", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idem_scope_key", name="uq_learning_events_idem"),
    )


def downgrade() -> None:
    op.drop_table("learning_events")
    op.drop_table("learning_sprints")
    op.drop_table("learning_contracts")
