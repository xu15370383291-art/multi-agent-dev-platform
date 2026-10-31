from alembic import context

from app.db import Base, engine


def include_name(name, type_, parent_names):
    # LangGraph owns and migrates its checkpoint tables independently.
    return not (
        type_ == "table"
        and name in {"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"}
    )


with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, include_name=include_name)
    with context.begin_transaction():
        context.run_migrations()
