"""System status endpoints for frontend readiness checks."""

from __future__ import annotations

from fastapi import APIRouter

from src.core.bootstrap import bootstrap_status, start_icon_bootstrap

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/bootstrap")
async def get_bootstrap_status():
    """Return current first-run setup status."""
    if not bootstrap_status.snapshot()["ready"]:
        start_icon_bootstrap()
    return bootstrap_status.snapshot()
