"""M2 R1: append-only failure ledger and idempotent recovery checks."""

import sqlalchemy as sa
from alembic import op

revision = "64bb01a3c201"
down_revision = "14c2cc51d06c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "run_failures",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("work_item_id", sa.String(36), sa.ForeignKey("work_items.id"), nullable=True),
        sa.Column("phase", sa.String(40), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("error_type", sa.String(40), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("next_step", sa.Text(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_failures_run_id", "run_failures", ["run_id"])
    op.create_table(
        "recovery_actions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("action_key", sa.String(100), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("expected_failure_id", sa.String(36), sa.ForeignKey("run_failures.id")),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("run_id", "action_key"),
        sa.CheckConstraint(
            "status IN ('queued','inspecting','ready','blocked','applying','succeeded','failed','cancelled')",
            name="ck_recovery_action_status",
        ),
    )
    op.create_index("ix_recovery_actions_run_id", "recovery_actions", ["run_id"])


def downgrade():
    for table in ("recovery_actions", "run_failures"):
        if op.get_bind().scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("m2_history_must_be_preserved")
    op.drop_table("recovery_actions")
    op.drop_table("run_failures")
