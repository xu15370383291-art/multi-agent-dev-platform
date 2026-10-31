"""M2 controller operation identities. Historical operations receive no fabricated receipts."""

import sqlalchemy as sa
from alembic import op

revision = "64bb01a3c204"
down_revision = "64bb01a3c203"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "execution_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("action_key", sa.String(100), nullable=False),
        sa.Column("tool_id", sa.String(36), sa.ForeignKey("tool_executions.id"), nullable=True, unique=True),
        sa.Column("owner", sa.String(36), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(64), nullable=True),
        sa.Column("image_id", sa.String(80), nullable=False),
        sa.Column("process_id", sa.String(36), sa.ForeignKey("execution_processes.id"), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "action_key"),
    )
    op.create_index("ix_execution_operations_run_id", "execution_operations", ["run_id"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM execution_operations")):
        raise RuntimeError("operation_history_must_be_preserved")
    op.drop_table("execution_operations")
