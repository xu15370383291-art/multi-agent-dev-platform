"""Keep fractional activity across pauses without changing the original time budget."""

import sqlalchemy as sa
from alembic import op

revision = "64bb01a3c203"
down_revision = "64bb01a3c202"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "workflow_runs", sa.Column("active_micros", sa.BigInteger(), nullable=False, server_default="0")
    )
    op.execute("UPDATE workflow_runs SET active_micros=active_seconds::bigint * 1000000")
    op.create_check_constraint("ck_run_active_micros_nonnegative", "workflow_runs", "active_micros >= 0")


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM workflow_runs WHERE active_micros != active_seconds::bigint * 1000000")
    ):
        raise RuntimeError("m2_fractional_activity_must_be_preserved")
    op.drop_constraint("ck_run_active_micros_nonnegative", "workflow_runs", type_="check")
    op.drop_column("workflow_runs", "active_micros")
