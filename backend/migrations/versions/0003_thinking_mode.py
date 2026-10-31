"""Versioned reasoning configuration, preserving historical factory behavior."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "model_profiles",
        sa.Column("thinking_mode", sa.String(30), nullable=False, server_default="provider_default"),
    )
    # Old official Qwen revisions actually ran disabled. Preserve that immutable
    # history; choosing default/enabled creates a new revision and clears checks.
    op.execute("""UPDATE model_profiles SET thinking_mode = 'disabled'
        WHERE lower(model_id) LIKE 'qwen%' AND
        (base_url LIKE 'https://dashscope.aliyuncs.com/%'
        OR base_url LIKE 'https://dashscope-intl.aliyuncs.com/%'
        OR base_url LIKE 'https://dashscope-us.aliyuncs.com/%'
        OR base_url LIKE 'https://cn-hongkong.dashscope.aliyuncs.com/%'
        OR base_url LIKE 'https://%.maas.aliyuncs.com/%')""")
    # Adapter/tool strategy changed. Old checks do not certify the new strategy.
    op.execute("UPDATE model_profiles SET capabilities = '{}'::json")


def downgrade():
    raise RuntimeError("不能删除思考模式审计信息；请保留数据库并采用前向修复")
