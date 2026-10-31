"""Append-only continuation and model result identities."""

import sqlalchemy as sa
from alembic import op

revision = "64bb01a3c205"
down_revision = "64bb01a3c204"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("model_invocations", sa.Column("epoch", sa.Integer(), nullable=True))
    op.add_column("model_invocations", sa.Column("request_hash", sa.String(64), nullable=True))
    op.add_column("recovery_actions", sa.Column("expected_epoch", sa.Integer(), nullable=True))
    op.add_column("recovery_actions", sa.Column("checkpoint_id", sa.String(80), nullable=True))
    op.add_column("recovery_actions", sa.Column("baseline_hash", sa.String(64), nullable=True))
    op.add_column("project_documents", sa.Column("generation_hash", sa.String(64), nullable=True))
    op.add_column("project_documents", sa.Column("payload_hash", sa.String(64), nullable=True))
    op.add_column(
        "task_executions",
        sa.Column("parent_execution_id", sa.String(36), sa.ForeignKey("task_executions.id"), nullable=True),
    )
    op.add_column(
        "task_executions",
        sa.Column("recovery_action_id", sa.String(36), sa.ForeignKey("recovery_actions.id"), nullable=True),
    )
    op.add_column("task_executions", sa.Column("continuation_summary_hash", sa.String(64), nullable=True))
    op.create_table(
        "developer_sessions",
        sa.Column("execution_id", sa.String(36), sa.ForeignKey("task_executions.id"), primary_key=True),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("invalid_completion_attempts", sa.Integer(), nullable=False),
        sa.Column("final_result", sa.JSON(), nullable=False),
        sa.Column("neutral_context", sa.JSON(), nullable=False),
    )
    op.create_table(
        "structured_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("profile_revision_id", sa.String(36), sa.ForeignKey("model_profiles.id"), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "request_hash"),
    )
    op.create_index("ix_structured_results_run_id", "structured_results", ["run_id"])
    op.create_table(
        "model_switch_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("action_key", sa.String(100), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column(
            "old_profile_revision_id", sa.String(36), sa.ForeignKey("model_profiles.id"), nullable=False
        ),
        sa.Column(
            "target_profile_revision_id", sa.String(36), sa.ForeignKey("model_profiles.id"), nullable=False
        ),
        sa.Column("target_signature", sa.String(64), nullable=False),
        sa.Column("expected_execution_id", sa.String(36), sa.ForeignKey("task_executions.id"), nullable=True),
        sa.Column("new_execution_id", sa.String(36), sa.ForeignKey("task_executions.id"), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "action_key"),
    )
    op.create_index("ix_model_switch_requests_run_id", "model_switch_requests", ["run_id"])


def downgrade():
    bind = op.get_bind()
    if bind.scalar(
        sa.text("SELECT count(*) FROM model_invocations WHERE epoch IS NOT NULL OR request_hash IS NOT NULL")
    ):
        raise RuntimeError("m2_invocation_history_must_be_preserved")
    for table in ("developer_sessions", "structured_results", "model_switch_requests"):
        if bind.scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("m2_continuation_history_must_be_preserved")
    if (
        bind.scalar(
            sa.text(
                "SELECT count(*) FROM task_executions WHERE parent_execution_id IS NOT NULL OR recovery_action_id IS NOT NULL OR continuation_summary_hash IS NOT NULL"
            )
        )
        or bind.scalar(
            sa.text(
                "SELECT count(*) FROM project_documents WHERE generation_hash IS NOT NULL OR payload_hash IS NOT NULL"
            )
        )
        or bind.scalar(
            sa.text(
                "SELECT count(*) FROM recovery_actions WHERE expected_epoch IS NOT NULL OR checkpoint_id IS NOT NULL OR baseline_hash IS NOT NULL"
            )
        )
    ):
        raise RuntimeError("m2_continuation_history_must_be_preserved")
    for table in ("model_switch_requests", "structured_results", "developer_sessions"):
        op.drop_table(table)
    for column in ("continuation_summary_hash", "recovery_action_id", "parent_execution_id"):
        op.drop_column("task_executions", column)
    op.drop_column("project_documents", "generation_hash")
    op.drop_column("project_documents", "payload_hash")
    for column in ("baseline_hash", "checkpoint_id", "expected_epoch"):
        op.drop_column("recovery_actions", column)
    for column in ("request_hash", "epoch"):
        op.drop_column("model_invocations", column)
