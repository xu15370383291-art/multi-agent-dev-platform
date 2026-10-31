"""Separate-origin static delivery preview. No workflow, credentials or write routes."""

import io
import mimetypes
import zipfile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from app import db as storage
from app.db import Candidate, Delivery, Run
from app.delivery import delivery_bytes

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/preview/{delivery_id}/{path:path}")
def preview(delivery_id: str, path: str, request: Request):
    if request.url.hostname not in {"127.0.0.1", "localhost"}:
        raise HTTPException(403)
    path = path or "index.html"
    if any(part in ("..", ".") for part in path.split("/")) or "\\" in path:
        raise HTTPException(404)
    with storage.Session() as db:
        delivery = db.get(Delivery, delivery_id)
        if not delivery:
            raise HTTPException(404)
        candidate = db.get(Candidate, delivery.candidate_id)
        try:
            content = delivery_bytes(delivery, db.get(Run, candidate.run_id))
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                value = archive.read("web/" + path)
        except KeyError:
            raise HTTPException(404) from None
    return Response(
        value,
        media_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
        headers={
            "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
