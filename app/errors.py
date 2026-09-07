"""One error shape for the whole API, plus the GitHub -> HTTP mapping.

Every failure the service produces looks like::

    {"error": {"code": "...", "message": "...", "details": {...}}}

Raw GitHub response bodies are never forwarded; only whitelisted, reshaped
fields make it into ``details``.
"""

import logging
import time
from typing import Any

import httpx
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

DEFAULT_RETRY_AFTER_SECONDS = 60
GITHUB_UNAVAILABLE_MESSAGE = "GitHub is temporarily unavailable. Please retry shortly."


class AppError(Exception):
    """An error we intend to return to the caller, already mapped to HTTP."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers

    def body(self) -> dict[str, Any]:
        return error_body(self.code, self.message, self.details)


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the error envelope."""
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"error": error}


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    """GitHub's body as a dict, or an empty dict if it is not JSON we can use."""
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def is_rate_limited(response: httpx.Response) -> bool:
    """Detect a rate-limit response.

    A 429 is unambiguous and always counts, even when GitHub sends no
    corroborating headers or message.

    GitHub does not always use 429, though: primary-limit rejections commonly
    arrive as 403 with ``x-ratelimit-remaining: 0``, and secondary limits as 403
    with ``retry-after``. A 403 therefore needs one of those signals, so an
    ordinary permission error stays a 403.
    """
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    if response.headers.get("x-ratelimit-remaining") == "0":
        return True
    if response.headers.get("retry-after"):
        return True
    message = str(_safe_json(response).get("message", "")).lower()
    return "rate limit" in message


def retry_after_seconds(response: httpx.Response, now: float | None = None) -> int:
    """Seconds the caller should wait, from ``Retry-After`` or the reset epoch."""
    raw = response.headers.get("retry-after")
    if raw:
        try:
            return max(0, int(float(raw)))
        except ValueError:
            pass
    reset = response.headers.get("x-ratelimit-reset")
    if reset:
        try:
            return max(0, int(float(reset)) - int(now if now is not None else time.time()))
        except ValueError:
            pass
    return DEFAULT_RETRY_AFTER_SECONDS


def _validation_details(response: httpx.Response) -> dict[str, Any] | None:
    """Whitelist the useful parts of a GitHub 422 body."""
    errors = _safe_json(response).get("errors")
    if not isinstance(errors, list):
        return None
    fields = [
        {key: str(item[key]) for key in ("resource", "field", "code") if key in item}
        for item in errors[:10]
        if isinstance(item, dict)
    ]
    fields = [field for field in fields if field]
    return {"github_errors": fields} if fields else None


def map_github_response(response: httpx.Response) -> AppError:
    """Translate a failed GitHub response into our own error."""
    status = response.status_code

    if status == 401:
        return AppError(
            401,
            "github_unauthorized",
            "GitHub authentication failed. Check that GITHUB_TOKEN is set and still valid.",
        )

    if is_rate_limited(response):
        retry_after = retry_after_seconds(response)
        return AppError(
            429,
            "rate_limited",
            "GitHub API rate limit exceeded.",
            {"retry_after": retry_after},
            {"Retry-After": str(retry_after)},
        )

    if status == 403:
        return AppError(
            403,
            "github_forbidden",
            "GitHub denied access to this resource. Check the token's repository "
            "permissions (Issues: Read and write).",
        )

    if status == 404:
        return AppError(404, "not_found", "The requested GitHub resource was not found.")

    if status == 422:
        message = str(_safe_json(response).get("message") or "validation failed")
        return AppError(
            400,
            "github_validation_failed",
            f"GitHub rejected the request: {message}",
            _validation_details(response),
        )

    if 400 <= status < 500:
        return AppError(400, "github_bad_request", "GitHub rejected the request.")

    return AppError(503, "github_unavailable", GITHUB_UNAVAILABLE_MESSAGE)


# --- FastAPI exception handlers ---------------------------------------------


async def app_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Render an :class:`AppError` in the standard envelope."""
    assert isinstance(exc, AppError)
    return JSONResponse(status_code=exc.status_code, content=exc.body(), headers=exc.headers)


async def validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Return 400 (not FastAPI's default 422) for request-validation failures."""
    assert isinstance(exc, RequestValidationError)
    fields = [
        {
            "loc": ".".join(str(part) for part in error.get("loc", ())),
            "msg": str(error.get("msg", "")),
            "type": str(error.get("type", "")),
        }
        for error in exc.errors()[:20]
    ]
    return JSONResponse(
        status_code=400,
        content=error_body(
            "invalid_request",
            "The request payload or parameters are invalid.",
            {"fields": fields} if fields else None,
        ),
    )


async def http_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Render Starlette's own errors (unknown path, wrong method) in our envelope."""
    assert isinstance(exc, StarletteHTTPException)
    codes = {404: "not_found", 405: "method_not_allowed"}
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(codes.get(exc.status_code, "http_error"), str(exc.detail)),
        headers=getattr(exc, "headers", None),
    )
