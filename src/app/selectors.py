from sqlalchemy import select

from src.app.models import Schedule, ScheduleFile
from src.selectors import BaseSelector


class ScheduleSelector(BaseSelector):
    async def get_all(self):
        result = await self._db.execute(select(Schedule))
        return result.scalars().all()

    async def get_by_id(self, *, object_id: int):
        result = await self._db.execute(select(Schedule).where(Schedule.id == object_id).limit(1))
        return result.scalars().first()

    async def get_by_section(self, *, section: str, order_by):
        result = await self._db.execute(select(Schedule).where(Schedule.section == section).order_by(order_by))
        return result.scalars().all()

    async def get_last_modified_by_section(self, *, section: str):
        result = await self._db.execute(
            select(Schedule.modified_at)
            .where(Schedule.section == section)
            .order_by(Schedule.modified_at.desc())
            .limit(1)
        )
        return result.scalars().first()

    async def get_distinct_by(self, distinct_field):
        result = await self._db.execute(select(Schedule).distinct(distinct_field).order_by(distinct_field.asc()))
        return result.scalars().all()


class ScheduleFileSelector(BaseSelector):
    async def get_all(self):
        result = await self._db.execute(select(ScheduleFile))
        return result.scalars().all()

    async def get_by_id(self, *, object_id: int):
        result = await self._db.execute(select(ScheduleFile).where(ScheduleFile.id == object_id).limit(1))
        return result.scalars().first()

    async def get_last_schedule_file(self):
        result = await self._db.execute(select(ScheduleFile).limit(1))
        return result.scalars().first()
