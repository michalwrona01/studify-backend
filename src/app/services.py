import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from fastapi import UploadFile
from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from ics import Calendar, Event
from ics.component import Component
from pydantic import EmailStr
from sqlalchemy import delete, insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models import Schedule, ScheduleFile
from src.app.schemas import ScheduleCreate
from src.app.selectors import ScheduleFileSelector


class ScheduleService:

    @staticmethod
    async def delete_and_create_schedules(schedules: List[ScheduleCreate], db: AsyncSession):
        async with db.begin():
            await db.execute(delete(Schedule))
            await db.execute(insert(Schedule), [s.model_dump() for s in schedules])

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

            for time_range, subject in entries:
                start, end = time_range.split("-")
                if not current_range:
                    current_range = {"start": start, "end": end, "name": subject.get("name", ""), "uid": subject.get("uid", "")}
                elif current_range["name"] == subject.get("name", "") and current_range["end"] == start:
                    current_range["end"] = end
                else:
                    aggregated.append(
                        (
                            f"{current_range['start']}-{current_range['end']}",
                            dict(name=current_range["name"], uid=current_range["uid"])
                        )
                    )
                    current_range = {"start": start, "end": end, "name": subject.get("name", ""), "uid": subject.get("uid", "")}

            if current_range:
                aggregated.append(
                    (
                        f"{current_range['start']}-{current_range['end']}", dict(name=current_range["name"], uid=current_range["uid"])
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
                event.name = subject.get("name", "")
                event.uid = subject.get("uid", "")
                calendar.events.add(event)

        return calendar

    @staticmethod
    def serialize_calendar(calendar: Calendar) -> str:
        return Component.serialize(calendar)


class ScheduleFileService:

    @staticmethod
    async def create_or_update_md5_file(*, file: UploadFile, db: AsyncSession) -> bool:
        """Returns boolean whether I should send an email"""
        schedule_file_selector = ScheduleFileSelector(db=db)

        content_file = await file.read()
        md5_hash = hashlib.md5(content_file).hexdigest()

        schedule_file = await schedule_file_selector.get_last_schedule_file()

        if not schedule_file:
            await db.execute(insert(ScheduleFile).values(md5_hash=md5_hash))
            return True

        if schedule_file and schedule_file.md5_hash != md5_hash:
            await db.execute(update(ScheduleFile).where(ScheduleFile.id == schedule_file.id).values(md5_hash=md5_hash))
            return True

        return False


class SMTPService:
    def __init__(self, config: ConnectionConfig):
        self.client = FastMail(config=config)

    async def send_mail(
        self,
        recipients: List[EmailStr],
        subject: str = "",
        body: Optional[Union[str, list]] = None,
        subtype: MessageType = "html",
        attachments: List[Union[UploadFile, Dict, str]] = None,
    ) -> bool:
        message = MessageSchema(
            subject=subject, recipients=recipients, body=body, subtype=subtype, attachments=attachments
        )
        await self.client.send_message(message)
        return True
