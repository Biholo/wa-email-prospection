from fastapi import APIRouter, Depends, Query

from core.auth import require_api_key
from core.db import get_logs

router = APIRouter(tags=["monitoring"])


@router.get("/logs")
async def logs(
    limit: int = Query(default=50, ge=1, le=500),
    status: str | None = Query(default=None, description="Filter by status: ENVOYÉ, SKIPPÉ, ERREUR, MAUVAISE_LISTE"),
    config: str | None = Query(default=None, description="Filter by config name, e.g. develly"),
    _: str = Depends(require_api_key),
):
    rows = get_logs(limit=limit, status_filter=status, config_filter=config)
    return {"logs": rows, "count": len(rows)}
