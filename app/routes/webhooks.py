"""GitHub webhook receiver.

Security and ordering both matter here:

1. the signature is checked against the *raw* bytes before anything is parsed;
2. the delivery id is required, because ``(delivery_id, action)`` is the
   deduplication key;
3. persistence failures are never acknowledged with 204.
"""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from app.config import Settings, get_settings
from app.db import record_delivery
from app.deps import get_db_path
from app.errors import AppError
from app.models import ErrorResponse
from app.webhook_security import (
    DELIVERY_HEADER,
    EVENT_HEADER,
    SIGNATURE_HEADER,
    verify_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhook"])

PING_ACTION = "ping"

#: Events we accept, and the actions we accept for each. Anything else is a 400.
SUPPORTED_ACTIONS: dict[str, frozenset[str]] = {
    "ping": frozenset(),
    "issues": frozenset(
        {
            "opened",
            "edited",
            "closed",
            "reopened",
            "deleted",
            "transferred",
            "pinned",
            "unpinned",
            "assigned",
            "unassigned",
            "labeled",
            "unlabeled",
            "locked",
            "unlocked",
            "milestoned",
            "demilestoned",
            "typed",
            "untyped",
            "field_added",
            "field_removed",
        }
    ),
    "issue_comment": frozenset({"created", "edited", "deleted"}),
}


def _issue_number(payload: dict[str, Any]) -> int | None:
    """Best-effort issue number; never raises on an unexpected payload shape."""
    issue = payload.get("issue")
    if isinstance(issue, dict):
        number = issue.get("number")
        if isinstance(number, int):
            return number
    return None


def _clip(value: str, limit: int = 50) -> str:
    return value[:limit]


@router.post(
    "/webhook",
    status_code=204,
    summary="Receive a GitHub webhook delivery",
    description=(
        "Verifies the `X-Hub-Signature-256` HMAC over the raw body, then records the "
        "delivery. Deduplicated on `(X-GitHub-Delivery, action)`, so GitHub redeliveries "
        "are safe no-ops. Returns an empty 204 on success."
    ),
    responses={
        204: {"description": "Delivery accepted (or already recorded)"},
        400: {"model": ErrorResponse, "description": "Unsupported event/action or bad payload"},
        401: {"model": ErrorResponse, "description": "Signature verification failed"},
        503: {"model": ErrorResponse, "description": "Delivery could not be persisted"},
    },
)
async def receive_webhook(
    request: Request,
    db_path: Annotated[str, Depends(get_db_path)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    raw_body = await request.body()
    event = (request.headers.get(EVENT_HEADER) or "").strip()
    delivery_id = (request.headers.get(DELIVERY_HEADER) or "").strip()

    # 1. Signature, over the exact bytes received. The signature itself, the
    #    secret, and the body are never logged.
    if not verify_signature(
        raw_body, request.headers.get(SIGNATURE_HEADER), settings.webhook_secret.get_secret_value()
    ):
        logger.warning(
            "webhook rejected",
            extra={"event": _clip(event), "outcome": "invalid_signature"},
        )
        raise AppError(401, "invalid_signature", "Webhook signature verification failed.")

    # 2. Event must be one we support.
    if event not in SUPPORTED_ACTIONS:
        logger.info(
            "webhook rejected",
            extra={"event": _clip(event), "outcome": "unsupported_event"},
        )
        raise AppError(400, "unsupported_event", f"Unsupported webhook event: '{_clip(event)}'.")

    # 3. Without a delivery id we cannot deduplicate, so we cannot process it.
    if not delivery_id:
        logger.info(
            "webhook rejected",
            extra={"event": event, "outcome": "missing_delivery_id"},
        )
        raise AppError(
            400,
            "missing_delivery_id",
            f"The {DELIVERY_HEADER} header is required.",
        )

    # 4. Payload must be a JSON object.
    try:
        payload = json.loads(raw_body or b"{}")
    except ValueError as exc:
        logger.info(
            "webhook rejected",
            extra={"event": event, "delivery_id": delivery_id, "outcome": "invalid_payload"},
        )
        raise AppError(400, "invalid_payload", "Webhook payload is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise AppError(400, "invalid_payload", "Webhook payload must be a JSON object.")

    # 5. Action must be one we support. `ping` carries none, so we store "ping"
    #    -- the dedupe key requires a non-null action.
    if event == PING_ACTION:
        action = PING_ACTION
    else:
        raw_action = payload.get("action")
        if not isinstance(raw_action, str) or raw_action not in SUPPORTED_ACTIONS[event]:
            logger.info(
                "webhook rejected",
                extra={
                    "event": event,
                    "delivery_id": delivery_id,
                    "outcome": "unsupported_action",
                },
            )
            raise AppError(
                400,
                "unsupported_action",
                f"Unsupported action for '{event}' event: "
                f"'{_clip(str(raw_action)) if raw_action is not None else ''}'.",
            )
        action = raw_action

    issue_number = _issue_number(payload)

    # 6. Persist. A failure here must not be acknowledged: returning 204 would
    #    tell GitHub the delivery was accepted and silently lose it.
    try:
        created = await record_delivery(
            db_path,
            delivery_id=delivery_id,
            event=event,
            action=action,
            issue_number=issue_number,
        )
    except Exception:
        logger.exception(
            "webhook persistence failed",
            extra={
                "event": event,
                "action": action,
                "delivery_id": delivery_id,
                "issue_number": issue_number,
                "outcome": "storage_error",
            },
        )
        raise AppError(
            503,
            "storage_unavailable",
            "The delivery could not be stored. GitHub may safely redeliver it.",
        ) from None

    logger.info(
        "webhook processed",
        extra={
            "event": event,
            "action": action,
            "delivery_id": delivery_id,
            "issue_number": issue_number,
            "duplicate": not created,
            "outcome": "processed",
        },
    )
    return Response(status_code=204)
