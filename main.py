from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from api.router import router
from core.db import init_db
# from scheduler.jobs import init_scheduler  # décommenter pour réactiver les crons

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # scheduler = init_scheduler()  # décommenter pour réactiver les crons
    yield
    # scheduler.shutdown(wait=False)  # décommenter pour réactiver les crons


app = FastAPI(
    title="Develly B2B Prospecting Server",
    description="Automated B2B outreach pipeline — WhatsApp, SMS, Brevo automations",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(router)
