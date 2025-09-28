from abc import ABC
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models import Schedule
from src.selectors import BaseSelector


class ScheduleBaseSelector(BaseSelector, ABC):
    pass


class ScheduleSelector(ScheduleBaseSelector):
    def __init__(self, *, db: AsyncSession):
        self._db = db

    async def get_all(self):
        result = await self._db.execute(select(Schedule))
        return result.scalars().all()

    async def get_by_id(self, *, object_id: int):
        result = await self._db.execute(select(Schedule).where(Schedule.id == object_id))
        return result.scalars().first()

    async def get_by_section(self, *, section: int, order_by):
        result = await self._db.execute(select(Schedule).where(Schedule.section == section).order_by(order_by))
        return result.scalars().all()

    async def get_distinct_by(self, distinct_field):
        result = await self._db.execute(select(Schedule).distinct(distinct_field).order_by(distinct_field.asc()))
        return result.scalars().all()
