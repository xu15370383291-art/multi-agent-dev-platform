"""A verified result repair is a bounded child goal, never a reset of its parent."""

from contextlib import contextmanager
from contextvars import ContextVar

from app.db import Invocation
from app.execution import ExecutionError

CURRENT_REPAIR = ContextVar("verified_model_result_repair", default=None)


@contextmanager
def goal(run_id, profile, role, source):
    token = CURRENT_REPAIR.set(
        {
            "run_id": run_id,
            "profile": profile,
            "role": role,
            "source_id": source.id,
            "parent_hash": source.request_hash,
        }
    )
    try:
        yield
    finally:
        CURRENT_REPAIR.reset(token)


def verified(db, run, profile, role, execution_id=None):
    value = CURRENT_REPAIR.get()
    if value is None:
        return None
    if value.get("run_id") != run.id or value.get("profile") != profile or value.get("role") != role:
        raise ExecutionError("model_contract_request_repeated")
    source = db.get(Invocation, value.get("source_id"))
    if (
        not source
        or source.run_id != run.id
        or source.profile_revision_id != profile
        or source.role != role
        or source.execution_id != execution_id
        or source.status != "failed"
        or not source.request_hash
        or source.request_hash != value.get("parent_hash")
    ):
        raise ExecutionError("model_contract_request_repeated")
    from app.invalid_model_archive import read

    proof = source.usage.get("invalid_output_archive", {})
    saved = read(run, source, proof)
    if not saved.get("validation", {}).get("schema_errors"):
        raise ExecutionError("model_contract_request_repeated")
    return source.request_hash
