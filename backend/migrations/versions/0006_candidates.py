"""Version-bound candidates with persistent build intent and evidence."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "execution_id", sa.String(36), sa.ForeignKey("task_executions.id"), nullable=False, unique=True
        ),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("prd_id", sa.String(36), sa.ForeignKey("project_documents.id"), nullable=False),
        sa.Column("architecture_id", sa.String(36), sa.ForeignKey("project_documents.id"), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("source_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("build_snapshot_hash", sa.String(64), nullable=True),
        sa.Column("lock_hash", sa.String(64), nullable=True),
        sa.Column("environment", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("build_result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    raise RuntimeError("候选与构建证据不可删除；请使用前向迁移")
