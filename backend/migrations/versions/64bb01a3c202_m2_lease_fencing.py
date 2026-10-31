"""M2 R2: monotonic epochs and durable work-item claim metadata."""

import sqlalchemy as sa
from alembic import op

revision = "64bb01a3c202"
down_revision = "64bb01a3c201"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("workflow_runs", sa.Column("epoch", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("work_items", sa.Column("claim_epoch", sa.Integer(), nullable=True))
    op.add_column("work_items", sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("work_items", sa.Column("payload_hash", sa.String(64), nullable=True))
    for name in ("pause_requested_at", "cancel_requested_at"):
        op.add_column("workflow_runs", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "execution_processes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("owner", sa.String(36), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("container_name", sa.String(64), nullable=False),
        sa.Column("container_id", sa.String(64), nullable=True, unique=True),
        sa.Column("image_id", sa.String(80), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_execution_processes_run_id", "execution_processes", ["run_id"])


def downgrade():
    if op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM workflow_runs WHERE epoch > 0 OR pause_requested_at IS NOT NULL OR cancel_requested_at IS NOT NULL"
        )
    ) or (op.get_bind().scalar(sa.text("SELECT count(*) FROM execution_processes"))):
        raise RuntimeError("m2_lease_history_must_be_preserved")
    op.drop_table("execution_processes")
    for name in ("pause_requested_at", "cancel_requested_at"):
        op.drop_column("workflow_runs", name)
    for column in ("payload_hash", "attempt", "claim_epoch"):
        op.drop_column("work_items", column)
    op.drop_column("workflow_runs", "epoch")
