import hashlib
from datetime import datetime
from typing import List
from zoneinfo import ZoneInfo

from fastapi import Depends, File, UploadFile
from ics import Calendar, Event
from ics.component import Component
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models import Schedule, ScheduleFile
from src.app.selectors import ScheduleFileSelector
from src.database import get_db


class ScheduleService:

    @staticmethod
    def create_calendar(schedules: List[Schedule]) -> Calendar:
        calendar = Calendar()

        for schedule in schedules:
            entries = sorted(
                [(k, v) for k, v in schedule.hours.items()],
                key=lambda x: datetime.strptime(x[0].split("-")[0], "%H:%M"),
            )

            aggregated = []
            current_range = None

            for time_range, description in entries:
                start, end = time_range.split("-")
                if not current_range:
                    current_range = {"start": start, "end": end, "desc": description}
                elif current_range["desc"] == description and current_range["end"] == start:
                    current_range["end"] = end
                else:
                    aggregated.append(
                        (
                            f"{current_range['start']}-{current_range['end']}",
                            current_range["desc"],
                        )
                    )
                    current_range = {"start": start, "end": end, "desc": description}

            if current_range:
                aggregated.append(
                    (
                        f"{current_range['start']}-{current_range['end']}",
                        current_range["desc"],
                    )
                )

            result = dict(aggregated)
            for hours, subject in result.items():
                event = Event()
                start_time_str, end_time_str = hours.split("-")
                start_time = datetime.strptime(start_time_str, "%H:%M").time()
                end_time = datetime.strptime(end_time_str, "%H:%M").time()
                event.begin = datetime.combine(schedule.date, start_time, tzinfo=ZoneInfo("Europe/Warsaw"))
                event.end = datetime.combine(schedule.date, end_time, tzinfo=ZoneInfo("Europe/Warsaw"))
                event.name = subject
                calendar.events.add(event)

        return calendar

    @staticmethod
    def serialize_calendar(calendar: Calendar) -> str:
        return Component.serialize(calendar)


class ScheduleFileService:

    @staticmethod
    async def create_or_update_schedule(db: AsyncSession, file: UploadFile):
        content_file = await file.read()
        md5_hash = hashlib.md5(content_file).hexdigest()

        old_schedule = await ScheduleFileSelector(db=db).get_last_schedule_file()

        if not old_schedule:
            db_schedule = ScheduleFile(md5_hash=md5_hash)
            db.add(db_schedule)
            try:
                await db.commit()
                await db.refresh(db_schedule)
                return db_schedule, True
            except Exception:
                await db.rollback()
                raise

        if old_schedule.md5_hash != md5_hash:
            old_schedule.md5_hash = md5_hash
            try:
                await db.commit()
                await db.refresh(old_schedule)
                return old_schedule, True
            except Exception:
                await db.rollback()
                raise

        return old_schedule, False
