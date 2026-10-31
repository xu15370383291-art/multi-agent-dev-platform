"""Read committed durable events using project-local commit-ordered watermarks."""

import asyncio
import base64
import json
import threading
import time
from collections import Counter

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app import db as storage
from app.db import Event, EventHead, Project, Run
from app.public_projection import public_value


def encode(project_id, run_id, seq):
    return base64.urlsafe_b64encode(
        json.dumps({"v": 1, "p": project_id, "r": run_id, "s": seq}, separators=(",", ":")).encode()
    ).decode()


def decode(cursor, project_id, run_id):
    try:
        if not cursor or len(cursor) > 1024:
            raise ValueError
        value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (
            set(value) != {"v", "p", "r", "s"}
            or value["v"] != 1
            or value["p"] != project_id
            or value["r"] != run_id
            or type(value["s"]) is not int
            or value["s"] < 0
        ):
            raise ValueError
        return value["s"]
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("event_stream_cursor_invalid") from None


def watermark(db, project_id, run_id):
    if not db.get(Project, project_id):
        raise HTTPException(404, "项目不存在")
    if run_id:
        run = db.get(Run, run_id)
        if not run or run.project_id != project_id:
            raise HTTPException(404, "运行不属于此项目")
    head = db.get(EventHead, project_id)
    return head.last_seq if head else 0


def public(row):
    return {
        **public_value({key: getattr(row, key) for key in ("id", "kind", "data", "run_id", "stream_seq")}),
        "created_at": row.created_at.isoformat(),
    }


def changes(project_id, run_id, cursor, page_size=100):
    with storage.Session() as db:
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        head = watermark(db, project_id, run_id)
        position = decode(cursor, project_id, run_id) if cursor else head
        if position > head:
            raise ValueError("event_stream_cursor_ahead")
        query = select(Event).where(
            Event.project_id == project_id, Event.stream_seq > position, Event.stream_seq <= head
        )
        if run_id:
            query = query.where(Event.run_id == run_id)
        rows = list(db.scalars(query.order_by(Event.stream_seq).limit(page_size + 1)))
        more = len(rows) > page_size
        rows = rows[:page_size]
        next_seq = rows[-1].stream_seq if more else head
        return {
            "items": [public(row) for row in rows],
            "cursor": encode(project_id, run_id, next_seq),
            "has_more": more,
        }


def history(project_id, run_id, cursor, page_size=100):
    with storage.Session() as db:
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        head = watermark(db, project_id, run_id)
        bound, before = head, head + 1
        if cursor:
            try:
                value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                if (
                    set(value) != {"v", "p", "r", "s", "b"}
                    or value["v"] != 2
                    or value["p"] != project_id
                    or value["r"] != run_id
                    or type(value["s"]) is not int
                    or type(value["b"]) is not int
                    or not 0 < value["s"] <= value["b"] <= head
                ):
                    raise ValueError
                bound, before = value["b"], value["s"]
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise ValueError("event_history_cursor_invalid") from None
        query = select(Event).where(
            Event.project_id == project_id, Event.stream_seq < before, Event.stream_seq <= bound
        )
        if run_id:
            query = query.where(Event.run_id == run_id)
        rows = list(db.scalars(query.order_by(Event.stream_seq.desc()).limit(page_size + 1)))
        more = len(rows) > page_size
        rows = rows[:page_size]
        next_cursor = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {"v": 2, "p": project_id, "r": run_id, "s": rows[-1].stream_seq, "b": bound}
                ).encode()
            ).decode()
            if more
            else None
        )
        return {
            "items": [public(row) for row in rows],
            "next_cursor": next_cursor,
            "head_cursor": encode(project_id, run_id, bound),
        }


class Admission:
    def __init__(self):
        self.lock = threading.Lock()
        self.projects = Counter()

    def acquire(self, project):
        with self.lock:
            if sum(self.projects.values()) >= 32 or self.projects[project] >= 8:
                raise HTTPException(429, "实时连接数量已达上限，请稍后重连", headers={"Retry-After": "5"})
            self.projects[project] += 1
        released = False

        def release():
            nonlocal released
            with self.lock:
                if not released:
                    self.projects[project] -= 1
                    if not self.projects[project]:
                        del self.projects[project]
                    released = True

        return release


admission = Admission()


def frame(kind, body, cursor=None):
    data = json.dumps(body, ensure_ascii=False, default=str, separators=(",", ":"))
    return (f"id: {cursor}\n" if cursor else "") + f"event: {kind}\ndata: {data}\n\n"


async def stream(request, project_id, run_id, initial, release, *, heartbeat=15, poll=0.5):
    cursor = initial
    last_heartbeat = time.monotonic()
    try:
        yield "retry: 1500\n\n" + frame("connected", {"cursor": cursor}, cursor)
        while not await request.is_disconnected():
            try:
                page = await asyncio.to_thread(changes, project_id, run_id, cursor)
            except (SQLAlchemyError, ValueError):
                yield frame("stream_problem", {"message": "事件连接暂时不可用，请按游标重新同步"})
                return
            for item in page["items"]:
                item_cursor = encode(project_id, run_id, item["stream_seq"])
                if len(json.dumps(item, ensure_ascii=False, default=str).encode()) > 65536:
                    item = {**item, "data": {"detail_truncated": True}}
                yield frame("project_event", item, item_cursor)
            previous_cursor, cursor = cursor, page["cursor"]
            # Scope-filtered events may advance the watermark without visible rows.
            if previous_cursor != cursor:
                yield frame("watermark", {"cursor": cursor}, cursor)
            if time.monotonic() - last_heartbeat >= heartbeat:
                yield ": heartbeat\n\n" + frame("heartbeat", {"cursor": cursor})
                last_heartbeat = time.monotonic()
            if not page["has_more"]:
                await asyncio.sleep(poll)
    finally:
        release()


class BoundedEventResponse(StreamingResponse):
    """One in-flight frame; release idle clients whose transport cannot drain."""

    send_timeout = 10

    async def stream_response(self, send):
        async def bounded(message):
            await asyncio.wait_for(send(message), timeout=self.send_timeout)

        try:
            await super().stream_response(bounded)
        except TimeoutError:
            # Persisted cursor replay replaces buffering for a slow consumer.
            try:
                await asyncio.wait_for(
                    send({"type": "http.response.body", "body": b"", "more_body": False}), 0.1
                )
            except (TimeoutError, OSError):
                pass
        finally:
            close = getattr(self.body_iterator, "aclose", None)
            if close:
                await close()
