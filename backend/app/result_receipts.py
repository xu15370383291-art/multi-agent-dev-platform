"""Validated structured outputs keyed by immutable request context, not graph position."""

import hashlib

from pydantic import BaseModel
from sqlalchemy import select

from app import db as storage
from app.db import StructuredResult
from app.execution import ExecutionError
from app.operation_receipts import canonical


def call(caller, profile_id, role, schema, context, run_id, guard):
    request_hash = hashlib.sha256(
        canonical(
            {"profile": profile_id, "role": role, "schema": schema.model_json_schema(), "context": context}
        )
    ).hexdigest()
    guard()
    with storage.Session() as db:
        prior = db.scalar(
            select(StructuredResult).where(
                StructuredResult.run_id == run_id, StructuredResult.request_hash == request_hash
            )
        )
        if prior:
            if (
                prior.profile_revision_id != profile_id
                or prior.role != role
                or hashlib.sha256(canonical(prior.result)).hexdigest() != prior.result_hash
            ):
                raise ExecutionError("structured_receipt_integrity_failed")
            return schema.model_validate(prior.result)
    result = caller(profile_id, role, schema, context, run_id, guard)
    # Model adapters may return a compatible base DTO rather than the dynamic
    # per-criterion subclass. Validate its data against the requested schema;
    # this still enforces required fields, exact AC literals and all evidence.
    result = schema.model_validate(result.model_dump() if isinstance(result, BaseModel) else result)
    guard()
    with storage.Session() as db, db.begin():
        db.add(
            StructuredResult(
                run_id=run_id,
                request_hash=request_hash,
                profile_revision_id=profile_id,
                role=role,
                result=result.model_dump(),
                result_hash=hashlib.sha256(canonical(result.model_dump())).hexdigest(),
            )
        )
    return result
