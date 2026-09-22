from fastapi import Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models import User
from src.database import get_db


async def require_calendar_subscription_token(
    token: str | None = Query(default=None), section: str = "1", db: AsyncSession = Depends(get_db)
) -> User:
    if token and len(token) == 43:
        user = await db.scalar(select(User).where(User.calendar_token == token))
        if user is not None:
            if not user.section or user.section != section:
                raise HTTPException(status_code=403, detail="Brak dostępu do tej sekcji.")
            return user
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid calendar subscription token",
    )


async def get_session_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    user_id = request.session.get("user_id")
    if isinstance(user_id, int):
        user = await db.get(User, user_id)
        if user and request.session.get("version") == user.session_version:
            return user
    return None


async def require_admin(user: User | None = Depends(get_session_user)) -> User:
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Dostęp tylko dla administratora.")
    return user
