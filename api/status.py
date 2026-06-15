from fastapi import APIRouter, Depends

from core.auth import require_api_key
from core.state import get_state

router = APIRouter(tags=["monitoring"])


@router.get("/status")
async def status(_: str = Depends(require_api_key)):
    return get_state()
