from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.database import get_db
from src.health_check.services import HealthCheckService

router = APIRouter(prefix="/health-check", tags=["health-check"])


@router.get("/liveness")
async def liveness_probe():
    return {"status": "alive"}


@router.get("/readiness")
async def readiness_probe(db: AsyncSession = Depends(get_db)):
    service = HealthCheckService(db)
    return await service.readiness_probe()
