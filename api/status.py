from fastapi import APIRouter

from core.state import get_state

router = APIRouter(tags=["monitoring"])


@router.get("/status")
async def status():
    return get_state()
