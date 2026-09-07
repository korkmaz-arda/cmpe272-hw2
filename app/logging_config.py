"""Structured (JSON) logging with a per-request correlation id.

Secrets never reach this module: the GitHub token and webhook secret are held in
``SecretStr``, and callers are responsible for passing only safe fields as
``extra``. Raw webhook bodies and signatures are never logged anywhere.
"""

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

REQUEST_ID_HEADER = "X-Request-ID"

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Attributes present on every LogRecord; anything else was passed via `extra`.
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime",
    "message",
    "taskName",
}


def get_request_id() -> str:
    """The correlation id for the request currently being handled."""
    return request_id_var.get()


def new_request_id() -> str:
    """Generate a fresh correlation id."""
    return uuid.uuid4().hex


class JsonFormatter(logging.Formatter):
    """Render each record as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": get_request_id(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON handler on the root and uvicorn loggers.

    Only handlers this function installed are replaced, so test-harness handlers
    (such as pytest's ``caplog``) survive re-configuration.
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_hw2_handler", False):
            root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._hw2_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)

    # Route uvicorn's own output through the same formatter.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
