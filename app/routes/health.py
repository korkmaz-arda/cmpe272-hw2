"""Liveness probe."""

from fastapi import APIRouter

from app.models import HealthOut

router = APIRouter(tags=["health"])


@router.get(
    "/healthz",
    response_model=HealthOut,
    summary="Liveness probe",
    description=(
        "Returns 200 while the process is serving. Deliberately does no GitHub or "
        "database work, so an upstream rate limit never reads as an unhealthy service."
    ),
)
async def healthz() -> HealthOut:
    return HealthOut(status="ok")
