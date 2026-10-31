"""Initial control-plane schema; fixed revision, no application create_all at startup."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Explicit columns keep this migration independent of future ORM changes.
    op.create_table(
        "projects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("project_type", sa.String(30), nullable=False),
        sa.Column("requirement", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "model_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("profile_id", sa.String(36), nullable=False, index=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("protocol", sa.String(50), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("model_id", sa.String(150), nullable=False),
        sa.Column("credential_ref", sa.String(100), nullable=False),
        sa.Column("capabilities", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("profile_id", "revision"),
    )
    op.create_table(
        "model_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("profile_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("scope", "role"),
    )
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("phase", sa.String(40), nullable=False),
        sa.Column("reason", sa.String(100)),
        sa.Column("model_calls", sa.Integer, nullable=False),
        sa.Column("max_model_calls", sa.Integer, nullable=False),
        sa.Column("max_seconds", sa.Integer, nullable=False),
        sa.Column("owner", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "idempotency_key"),
    )
    op.create_table(
        "project_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("markdown", sa.Text, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("run_id", "kind", "version"),
    )
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column(
            "document_id", sa.String(36), sa.ForeignKey("project_documents.id"), nullable=False, unique=True
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("decision", sa.String(30)),
        sa.Column("feedback", sa.Text, nullable=False),
    )
    op.create_table(
        "work_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("action_key", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "action_key"),
    )
    op.create_table(
        "project_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id")),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "model_invocations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("workflow_runs.id")),
        sa.Column("profile_revision_id", sa.String(36), sa.ForeignKey("model_profiles.id"), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("usage", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    raise RuntimeError(
        "Initial data deletion is deliberately not automated; restore a database backup instead."
    )
