"""Versioned progress policy; legacy caps and ledgers retained."""

import sqlalchemy as sa
from alembic import op

revision = "64bb01a3c207"
down_revision = "64bb01a3c206"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("developer_sessions", sa.Column("messages", sa.JSON(), server_default="[]", nullable=False))
    op.add_column(
        "workflow_runs", sa.Column("call_policy", sa.String(30), server_default="fixed", nullable=False)
    )
    op.add_column(
        "workflow_runs", sa.Column("invocation_policy", sa.JSON(), server_default="{}", nullable=False)
    )
    op.alter_column("workflow_runs", "max_model_calls", nullable=True)
    op.create_check_constraint(
        "ck_run_call_policy_limit",
        "workflow_runs",
        "(call_policy = 'fixed' AND max_model_calls > 0 AND max_model_calls IS NOT NULL) OR (call_policy = 'progress_guarded' AND max_model_calls IS NULL)",
    )
    op.create_table(
        "progress_states",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), primary_key=True),
        sa.Column("scope", sa.String(64), primary_key=True),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_table(
        "progress_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "scope", "sequence"),
    )
    op.create_index("ix_progress_records_run_id", "progress_records", ["run_id"])


def downgrade():
    used = op.get_bind().scalar(
        sa.text("""SELECT
        EXISTS(SELECT 1 FROM progress_states)
        OR EXISTS(SELECT 1 FROM progress_records)
        OR EXISTS(SELECT 1 FROM workflow_runs WHERE call_policy <> 'fixed'
            OR invocation_policy::jsonb <> '{}'::jsonb)
        OR EXISTS(SELECT 1 FROM developer_sessions WHERE messages::jsonb <> '[]'::jsonb)
        OR EXISTS(SELECT 1 FROM project_events WHERE kind LIKE 'progress.%'
            OR kind = 'run.call_policy_migrated')""")
    )
    if used:
        raise RuntimeError("progress_policy_history_requires_preservation")
    op.drop_index("ix_progress_records_run_id", table_name="progress_records")
    op.drop_table("progress_records")
    op.drop_table("progress_states")
    op.drop_constraint("ck_run_call_policy_limit", "workflow_runs", type_="check")
    op.alter_column("workflow_runs", "max_model_calls", nullable=False)
    op.drop_column("workflow_runs", "invocation_policy")
    op.drop_column("workflow_runs", "call_policy")
    op.drop_column("developer_sessions", "messages")
