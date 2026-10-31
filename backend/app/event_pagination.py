"""Bounded, project/run-bound cursors over the durable event timeline."""

import base64
import json
import uuid
from datetime import datetime

from sqlalchemy import select, tuple_

from app.db import Event


def page(db, project_id, run_id, page_size, cursor):
    query = select(Event).where(Event.project_id == project_id)
    if run_id:
        query = query.where(Event.run_id == run_id)
    if cursor:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if set(value) != {"project", "run", "at", "id"}:
                raise ValueError
            if value["project"] != project_id or value["run"] != run_id:
                raise ValueError
            at = datetime.fromisoformat(value["at"])
            if not at.tzinfo:
                raise ValueError
            identity = str(uuid.UUID(value["id"]))
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise ValueError("event_cursor_invalid") from None
        query = query.where(tuple_(Event.created_at, Event.id) < tuple_(at, identity))
    rows = db.scalars(query.order_by(Event.created_at.desc(), Event.id.desc()).limit(page_size + 1)).all()
    more = len(rows) > page_size
    rows = rows[:page_size]
    next_cursor = None
    if more:
        last = rows[-1]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "project": project_id,
                    "run": run_id,
                    "at": last.created_at.isoformat(),
                    "id": last.id,
                }
            ).encode()
        ).decode()
    keys = ["id", "kind", "data", "created_at", "run_id"]
    return {"items": [{key: getattr(row, key) for key in keys} for row in rows], "next_cursor": next_cursor}
