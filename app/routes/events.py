"""Recent webhook deliveries, for debugging and verifying the GitHub webhook."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.db import recent_deliveries
from app.deps import get_db_path
from app.models import ErrorResponse, EventOut

router = APIRouter(tags=["events"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 200


@router.get(
    "/events",
    response_model=list[EventOut],
    summary="List recently processed webhook deliveries",
    responses={400: {"model": ErrorResponse, "description": "Invalid limit"}},
)
async def list_events(
    db_path: Annotated[str, Depends(get_db_path)],
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> list[EventOut]:
    rows = await recent_deliveries(db_path, limit)
    return [EventOut(**row) for row in rows]
