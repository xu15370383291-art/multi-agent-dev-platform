"""Nullable cumulative time/tool ceilings; migration alone never resumes business."""

import sqlalchemy as sa
from alembic import op

revision = "64bb01a3c208"
down_revision = "64bb01a3c207"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("workflow_runs", "max_seconds", nullable=True)
    op.alter_column("task_executions", "max_tool_calls", nullable=True)


def downgrade():
    if op.get_bind().scalar(
        sa.text("""SELECT
        EXISTS(SELECT 1 FROM workflow_runs WHERE max_seconds IS NULL
            OR invocation_policy::jsonb->>'version' = '2')
        OR EXISTS(SELECT 1 FROM task_executions WHERE max_tool_calls IS NULL)""")
    ):
        raise RuntimeError("global_progress_history_requires_preservation")
    op.alter_column("task_executions", "max_tool_calls", nullable=False)
    op.alter_column("workflow_runs", "max_seconds", nullable=False)
