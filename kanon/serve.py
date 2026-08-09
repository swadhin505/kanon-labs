"""Optional HTTP + MCP faces over the same in-memory :class:`Twin`.

The engine remains transport-free. This module only translates typed network
requests into ``Twin.call`` and provider-shaped refusals back into HTTP.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

from pydantic import BaseModel, ConfigDict, create_model

from kanon.gate.story import Fault
from kanon.twin import InjectedResponse, Pack, Twin, TwinError
from kanon.twin.store import Snapshot

_PYTHON_TYPES = {"string": str, "number": float, "integer": int, "boolean": bool}


class SnapshotBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    records: dict[str, dict[str, dict[str, Any]]]
    counters: dict[str, int]
    step: int


class ResetBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    given: dict[str, list[dict[str, Any]]] = {}
    faults: list[Fault] = []


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


def create_http_app(pack: Pack, twin: Twin | None = None, token: str | None = None):
    """Build a typed FastAPI app for one behavior pack."""
    try:
        from fastapi import FastAPI, Request
        from fastapi.exceptions import RequestValidationError
        from fastapi.responses import JSONResponse
        from starlette.exceptions import HTTPException as StarletteHTTPException
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise RuntimeError("HTTP serving needs `pip install -e .[serve]`") from exc

    twin = twin or Twin(pack)
    app = FastAPI(title=f"{pack.name} deterministic twin", version="0.0.1")
    app.state.twin = twin

    if token:

        @app.middleware("http")
        async def authenticate(request: Request, call_next):
            if request.headers.get("authorization") != f"Bearer {token}":
                return JSONResponse(
                    status_code=401,
                    content={"error": {"code": "unauthorized", "message": "invalid token"}},
                )
            return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_request", "message": str(exc)}},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "invalid_request", "message": str(exc.detail)}},
        )

    @app.exception_handler(TwinError)
    async def twin_error(request: Request, exc: TwinError):
        return JSONResponse(status_code=exc.status, content=exc.as_response())

    @app.post("/__admin/reset", include_in_schema=False)
    def reset(body: ResetBody | None = None) -> dict[str, bool]:
        twin.reset(
            body.given if body else None,
            [fault.model_dump() for fault in body.faults] if body else None,
        )
        return {"ok": True}

    @app.get("/__admin/snapshot", include_in_schema=False)
    def snapshot() -> dict[str, Any]:
        return asdict(twin.snapshot())

    @app.post("/__admin/restore", include_in_schema=False)
    def restore(body: SnapshotBody) -> dict[str, bool]:
        twin.restore(Snapshot(**body.model_dump()))
        return {"ok": True}

    @app.get("/__admin/state", include_in_schema=False)
    def state() -> dict[str, Any]:
        return twin.state()

    @app.get("/__admin/events", include_in_schema=False)
    def events(after: int = 0) -> dict[str, Any]:
        return {"events": twin.events(after), "cursor": len(twin.events())}

    @app.get("/__admin/coverage", include_in_schema=False)
    def coverage() -> dict[str, int]:
        return twin.uncovered

    @app.post("/__admin/begin", include_in_schema=False)
    def begin() -> dict[str, bool]:
        twin.begin_run()
        return {"ok": True}

    def make_invoke(selected: str, body_model):
        def invoke(body):
            try:
                result = twin.call(selected, body.model_dump(exclude_none=True))
                if isinstance(result, InjectedResponse):
                    return JSONResponse(status_code=result.status, content=result.body)
                return result
            except TwinError as refusal:
                return JSONResponse(status_code=refusal.status, content=refusal.as_response())

        invoke.__annotations__["body"] = body_model
        return invoke

    response_models = {}
    for index, (name, resource) in enumerate(sorted(pack.resources.items())):
        fields = {
            resource.id_field: (str, ...),
            **{
                field: (_PYTHON_TYPES[kind] | None, None)
                for field, kind in resource.fields.items()
                if field != resource.id_field
            },
        }
        if resource.state_field:
            fields[resource.state_field] = (str, ...)
        fields.update({field: (str | None, None) for field in resource.timestamps})
        response_models[name] = create_model(
            f"Resource{index}Response", __config__=ConfigDict(extra="forbid"), **fields
        )

    error_responses = {status: {"model": ErrorResponse} for status in (400, 404, 409, 422, 501)}
    for index, operation in enumerate(sorted(pack.routes)):
        arguments = pack.arguments(operation)
        required = set(pack.required_args(operation))
        fields = {
            name: (
                (_PYTHON_TYPES[kind], ...)
                if name in required
                else (_PYTHON_TYPES[kind] | None, None)
            )
            for name, kind in arguments.items()
        }
        request = create_model(
            f"Tool{index}Request",
            __config__=ConfigDict(extra="forbid", strict=True),
            **fields,
        )

        invoke = make_invoke(operation, request)
        invoke.__name__ = operation
        invoke.__doc__ = pack.routes[operation].description
        route = pack.routes[operation]
        response_model = response_models[route.resource]
        if route.verb == "list":
            response_model = list[response_model]
        app.add_api_route(
            f"/tools/{index}",
            invoke,
            methods=["POST"],
            operation_id=operation,
            summary=pack.routes[operation].description or operation,
            response_model=response_model,
            responses=error_responses,
        )

    return app


def create_app(pack: Pack, twin: Twin | None = None, token: str | None = None):
    """Serve HTTP operations and generated MCP tools from one ASGI app."""
    try:
        import httpx
        from fastapi import FastAPI
        from fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise RuntimeError("HTTP/MCP serving needs `pip install -e .[serve]`") from exc

    api = create_http_app(pack, twin, token)
    headers = {"Authorization": f"Bearer {token}"} if token else None
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://twin", headers=headers
    )
    mcp = FastMCP.from_openapi(openapi_spec=api.openapi(), client=client, name=pack.name)
    mcp_app = mcp.http_app(path="/mcp")

    @asynccontextmanager
    async def lifespan(app):
        async with client, mcp_app.lifespan(app):
            yield

    app = FastAPI(
        title=api.title,
        routes=[*mcp_app.routes, *api.routes],
        lifespan=lifespan,
    )
    app.state.twin = api.state.twin
    app.state.mcp = mcp
    if token:

        @app.middleware("http")
        async def authenticate(request, call_next):
            from fastapi.responses import JSONResponse

            if request.headers.get("authorization") != f"Bearer {token}":
                return JSONResponse(
                    status_code=401,
                    content={"error": {"code": "unauthorized", "message": "invalid token"}},
                )
            return await call_next(request)

    return app


def run(pack: Pack, host: str = "127.0.0.1", port: int = 8000) -> None:
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - exercised without the extra
        raise RuntimeError("serving needs `pip install -e .[serve]`") from exc
    token = os.environ.get("KANON_TWIN_TOKEN")
    if host not in {"127.0.0.1", "localhost", "::1"} and not token:
        raise RuntimeError("public serving requires KANON_TWIN_TOKEN")
    uvicorn.run(create_app(pack, token=token), host=host, port=port)
