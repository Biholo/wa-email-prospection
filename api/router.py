from fastapi import APIRouter

from api.audit import router as audit_router
from api.logs import router as logs_router
from api.status import router as status_router
from api.webhook_resend import router as webhook_resend_router
from api.webhook_whatsapp import router as webhook_whatsapp_router

router = APIRouter()
router.include_router(audit_router)
router.include_router(status_router)
router.include_router(logs_router)
router.include_router(webhook_resend_router)
router.include_router(webhook_whatsapp_router)
