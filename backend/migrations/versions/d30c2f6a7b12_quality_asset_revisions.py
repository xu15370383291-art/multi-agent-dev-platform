"""Append-only quality report and test asset revisions."""

import sqlalchemy as sa
from alembic import op

revision = "d30c2f6a7b12"
down_revision = "a441baa9e543"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("quality_reports", sa.Column("attempt", sa.Integer(), server_default="1", nullable=False))
    op.add_column("quality_reports", sa.Column("supersedes_id", sa.String(36), nullable=True))
    op.create_foreign_key(
        "fk_quality_supersedes", "quality_reports", "quality_reports", ["supersedes_id"], ["id"]
    )
    op.drop_constraint("quality_reports_candidate_id_role_key", "quality_reports", type_="unique")
    op.create_unique_constraint(
        "uq_quality_candidate_role_attempt", "quality_reports", ["candidate_id", "role", "attempt"]
    )


def downgrade():
    # Downgrade is intentionally refused when history would be lost.
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM quality_reports WHERE attempt > 1 LIMIT 1")).first():
        raise RuntimeError("quality_revision_history_must_be_preserved")
    op.drop_constraint("uq_quality_candidate_role_attempt", "quality_reports", type_="unique")
    op.create_unique_constraint(
        "quality_reports_candidate_id_role_key", "quality_reports", ["candidate_id", "role"]
    )
    op.drop_constraint("fk_quality_supersedes", "quality_reports", type_="foreignkey")
    op.drop_column("quality_reports", "supersedes_id")
    op.drop_column("quality_reports", "attempt")
