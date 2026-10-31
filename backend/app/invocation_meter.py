"""Worker invocation intent and budget consumption commit in one fenced transaction."""

from app import db as storage
from app.db import Invocation
from app.execution import ExecutionError
from app.execution_control import CURRENT_CONTROL
from app.lease_service import lock_valid
from app.plans import consume_call


def begin(run_id, profile_id, role, guard, execution_id=None, request_hash=None, request_metrics=None):
    control = CURRENT_CONTROL.get()
    if control:
        if control.run_id != run_id:
            raise ExecutionError("execution_control_identity_mismatch")
        guard()
        with storage.Session() as db, db.begin():
            run = lock_valid(db, run_id, control.owner)
            from app.model_contract_guard import check

            check(db, run_id, profile_id, role, request_hash)
            from app.model_repair_goal import verified as repair_goal
            from app.progress_guard import admit

            blocked = admit(
                db,
                run,
                role,
                execution_id,
                request_hash,
                repair_parent=repair_goal(db, run, profile_id, role, execution_id),
            )
            if not blocked:
                consume_call(db, run, execution_id)
                row = Invocation(
                    run_id=run_id,
                    profile_revision_id=profile_id,
                    role=role,
                    execution_id=execution_id,
                    epoch=run.epoch,
                    request_hash=request_hash,
                    usage={"request_metrics": request_metrics} if request_metrics else {},
                )
                db.add(row)
                db.flush()
                identity = row.id
        if blocked:
            from app.progress_guard import ProgressBlocked

            raise ProgressBlocked(blocked)
        return identity
    # Compatibility for isolated adapter callers with explicit guards. The platform
    # Worker always establishes CURRENT_CONTROL before invoking any role/model.
    from app.model_contract_guard import check

    with storage.Session() as db:
        check(db, run_id, profile_id, role, request_hash)
    guard(reserve_call=True)
    with storage.Session() as db, db.begin():
        row = Invocation(
            run_id=run_id,
            profile_revision_id=profile_id,
            role=role,
            execution_id=execution_id,
            request_hash=request_hash,
            usage={"request_metrics": request_metrics} if request_metrics else {},
        )
        db.add(row)
        db.flush()
        return row.id
