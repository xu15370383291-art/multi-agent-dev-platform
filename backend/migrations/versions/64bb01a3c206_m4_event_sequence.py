"""Project-local transactional event ordering, retaining every legacy event field."""

import sqlalchemy as sa
from alembic import op

revision = "64bb01a3c206"
down_revision = "64bb01a3c205"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_event_heads",
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), primary_key=True),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
    )
    op.add_column("project_events", sa.Column("stream_seq", sa.BigInteger(), nullable=True))
    op.execute("""WITH ranked AS (
        SELECT id, row_number() OVER (PARTITION BY project_id ORDER BY created_at, id) AS seq
        FROM project_events
    ) UPDATE project_events AS e SET stream_seq=r.seq FROM ranked AS r WHERE e.id=r.id""")
    op.execute("""INSERT INTO project_event_heads(project_id,last_seq)
        SELECT project_id,max(stream_seq) FROM project_events GROUP BY project_id""")
    op.alter_column("project_events", "stream_seq", nullable=False)
    op.create_unique_constraint("uq_event_project_sequence", "project_events", ["project_id", "stream_seq"])
    op.create_check_constraint("ck_event_positive_sequence", "project_events", "stream_seq > 0")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM project_events")):
        raise RuntimeError("event_stream_history_requires_preservation")
    op.drop_constraint("ck_event_positive_sequence", "project_events", type_="check")
    op.drop_constraint("uq_event_project_sequence", "project_events", type_="unique")
    op.drop_column("project_events", "stream_seq")
    op.drop_table("project_event_heads")
