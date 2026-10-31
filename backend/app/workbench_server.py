"""Local production workbench. Static platform assets; streaming API proxy only."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

HOP = {
    "host",
    "connection",
    "transfer-encoding",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "upgrade",
    "content-length",
}


def create_app(dist=None, target=None):
    root = Path(dist or os.environ.get("COMPANY_FRONTEND_DIST", "../frontend/dist")).resolve()
    upstream = target or os.environ.get("COMPANY_FRONTEND_API_TARGET", "http://127.0.0.1:8000")
    url = httpx.URL(upstream)
    if url.scheme != "http" or url.host not in {"localhost", "127.0.0.1", "::1"} or url.userinfo:
        raise ValueError("local_api_target_required")

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(
            base_url=upstream,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(15, read=25),
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
        ) as client:
            app.state.proxy = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_only(request, call_next):
        if request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return JSONResponse({"detail": "仅允许本机访问"}, status_code=403)
        return await call_next(request)

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy(path: str, request: Request):
        # Absolute URL syntax and query parameters can never select another target.
        path = "/api/" + path
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 2 * 1024 * 1024:
                raise HTTPException(413, "请求过大")
        connection_headers = {x.strip().lower() for x in request.headers.get("connection", "").split(",")}
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP | connection_headers}
        client = request.app.state.proxy
        outgoing = client.build_request(
            request.method, path, params=request.query_params, headers=headers, content=bytes(body)
        )
        try:
            response = await client.send(outgoing, stream=True)
        except httpx.HTTPError:
            return JSONResponse({"detail": "后端连接不可用，请检查服务状态"}, status_code=503)
        excluded = (
            HOP
            | {"content-encoding"}
            | {x.strip().lower() for x in response.headers.get("connection", "").split(",")}
        )
        return StreamingResponse(
            response.aiter_bytes(),
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k.lower() not in excluded},
            background=BackgroundTask(response.aclose),
        )

    @app.get("/{path:path}")
    async def static(path: str, request: Request):
        if not (root / "index.html").is_file():
            raise HTTPException(503, "工作台尚未构建，请先运行构建命令")
        if (
            path.startswith("api")
            or "\\" in path
            or ":" in path
            or any(part.startswith(".") for part in path.split("/") if part)
        ):
            raise HTTPException(404)
        file = (root / (path or "index.html")).resolve()
        if not file.is_relative_to(root):
            raise HTTPException(404)
        # Only extensionless SPA locations fall back. Missing JS must not return HTML.
        if not file.is_file() and not Path(path).suffix:
            file = root / "index.html"
        if not file.is_file():
            raise HTTPException(404)
        return FileResponse(file, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    return app


app = create_app()
