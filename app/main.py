"""Application factory, middleware, and exception wiring."""

import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.config import Settings, get_settings
from app.db import init_db
from app.errors import (
    AppError,
    app_error_handler,
    error_body,
    http_exception_handler,
    validation_error_handler,
)
from app.github_client import GitHubClient, build_http_client
from app.logging_config import (
    REQUEST_ID_HEADER,
    configure_logging,
    new_request_id,
    request_id_var,
)
from app.routes import events, health, issues, webhooks

logger = logging.getLogger(__name__)

# An inbound correlation id is reused only if it is short and boring, so it can
# never inject newlines or unbounded junk into the logs.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

DESCRIPTION = """
A small service that wraps the GitHub REST API for Issues in one configured
repository.

Callers of this API do not authenticate. The GitHub fine-grained token is a
server-side credential loaded from the `GITHUB_TOKEN` environment variable and is
never accepted from, or exposed to, clients.
"""


def _resolve_request_id(request: Request) -> str:
    incoming = request.headers.get(REQUEST_ID_HEADER, "")
    return incoming if _SAFE_REQUEST_ID.match(incoming) else new_request_id()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await init_db(settings.db_path)
        http_client = build_http_client(settings)
        app.state.settings = settings
        app.state.http_client = http_client
        app.state.github = GitHubClient(http_client, settings.github_owner, settings.github_repo)
        app.state.db_path = settings.db_path
        logger.info(
            "service starting",
            extra={"repo": settings.repo_full_name, "db_path": settings.db_path},
        )
        try:
            yield
        finally:
            await http_client.aclose()

    app = FastAPI(
        title="GitHub Issues API Wrapper",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _resolve_request_id(request)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                # A bug in our own code: log it in full, tell the caller nothing.
                logger.exception(
                    "unhandled error", extra={"method": request.method, "path": request.url.path}
                )
                response = JSONResponse(
                    status_code=500,
                    content=error_body("internal_error", "An unexpected internal error occurred."),
                )
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            return response
        finally:
            request_id_var.reset(token)

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    app.include_router(health.router)
    app.include_router(issues.router)
    app.include_router(webhooks.router)
    app.include_router(events.router)
    return app


app = create_app()


def main() -> None:  # pragma: no cover - process entry point
    """Run the service with uvicorn, honouring the required PORT variable."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_config=None)  # noqa: S104


if __name__ == "__main__":  # pragma: no cover
    main()
