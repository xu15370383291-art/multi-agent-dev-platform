"""Traceable command evidence and per-task model invocation links."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tool_executions", sa.Column("request", sa.JSON(), nullable=False, server_default="{}"))
    op.alter_column("tool_executions", "request", server_default=None)
    op.add_column("model_invocations", sa.Column("execution_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_invocation_task_execution", "model_invocations", "task_executions", ["execution_id"], ["id"]
    )


def downgrade():
    raise RuntimeError("工具证据与模型执行身份不可删除；请使用前向迁移")
