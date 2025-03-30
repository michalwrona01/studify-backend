from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class HealthCheckService:
    """Health check service"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def readiness_probe(self):
        try:
            await self.db.execute(select(1))
        except Exception as e:
            return {"status": "error", "details": str(e)}, 503
        else:
            return {"status": "ok"}
