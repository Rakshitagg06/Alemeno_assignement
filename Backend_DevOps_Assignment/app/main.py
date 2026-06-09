import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import create_tables
from app.routers.jobs import router as jobs_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting API and ensuring database schema")
    create_tables()
    yield


app = FastAPI(title="AI-Powered Transaction Processing Pipeline", lifespan=lifespan)
app.include_router(jobs_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

