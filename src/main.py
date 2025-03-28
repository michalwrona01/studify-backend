from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db

app = FastAPI()


@app.get("/health-check")
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(select(1))
    except Exception as e:
        return {"status": "error", "details": str(e)}
    else:
        return {"status": "ok"}
