"""Exclude human approval waiting time from execution budget."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workflow_runs", sa.Column("active_seconds", sa.Integer, nullable=False, server_default="0")
    )
    op.add_column("workflow_runs", sa.Column("active_started_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("workflow_runs", "active_started_at")
    op.drop_column("workflow_runs", "active_seconds")
