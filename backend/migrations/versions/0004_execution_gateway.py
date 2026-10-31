"""Persistent task identities and write-ahead tool execution records."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "task_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("task_id", sa.String(100), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("profile_revision_id", sa.String(36), sa.ForeignKey("model_profiles.id"), nullable=False),
        sa.Column("prd_id", sa.String(36), sa.ForeignKey("project_documents.id"), nullable=False),
        sa.Column("architecture_id", sa.String(36), sa.ForeignKey("project_documents.id"), nullable=False),
        sa.Column("base_commit_sha", sa.String(64), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("max_tool_calls", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "task_id", "attempt"),
    )
    op.create_table(
        "tool_executions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("task_executions.id"), nullable=False),
        sa.Column("tool_call_id", sa.String(100), nullable=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("lease_token", sa.String(36), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("execution_id", "tool_call_id"),
    )


def downgrade():
    raise RuntimeError("执行审计记录不可删除；请使用前向迁移")
