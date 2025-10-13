import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from fastapi import UploadFile
from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from icalendar import Calendar as iCalendar
from icalendar import Event as iEvent
from icalendar import vText
from ics import Calendar as ICSCalendar
from ics import Event as ICSEvent
from pydantic import EmailStr
from sqlalchemy import delete, insert, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models import Schedule, ScheduleFile
from src.app.schemas import ScheduleCreate
from src.app.selectors import ScheduleFileSelector


class CalendarBaseService(ABC):
    @staticmethod
    @abstractmethod
    def serialize_calendar(schedules: List[Dict]) -> str:
        pass


class ICSService(CalendarBaseService):
    @staticmethod
    def serialize_calendar(schedules: List[Dict]) -> str:
        calendar = ICSCalendar()
        for day in schedules:
            for hours, subject in day.items():
                event = ICSEvent()
                start_time_str, end_time_str = hours.split("-")
                start_time = datetime.strptime(start_time_str, "%H:%M").time()
                end_time = datetime.strptime(end_time_str, "%H:%M").time()
                event.begin = datetime.combine(subject.get("day_begin"), start_time, tzinfo=ZoneInfo("Europe/Warsaw"))
                event.end = datetime.combine(subject.get("day_end"), end_time, tzinfo=ZoneInfo("Europe/Warsaw"))
                event.name = subject.get("name", "")
                event.uid = subject.get("uid", "")
                calendar.events.add(event)

        return calendar.serialize()


class ICalendarService(CalendarBaseService):
    @staticmethod
    def serialize_calendar(schedules: List[Dict]) -> str:
        calendar = iCalendar()

        calendar.add("prodid", "-//Lekarski//Semestr//7//PL")
        calendar.add("version", "2.0")
        calendar.add("calscale", "GREGORIAN")
        calendar.add("method", "PUBLISH")

        tz = ZoneInfo("Europe/Warsaw")

        for day in schedules:
            for hours, subject in day.items():
                event = iEvent()
                calendar.add_component(event)

                start_time_str, end_time_str = hours.split("-")
                start_time = datetime.strptime(start_time_str, "%H:%M").time()
                end_time = datetime.strptime(end_time_str, "%H:%M").time()

                dtstart = datetime.combine(subject.get("day_begin"), start_time, tzinfo=tz)
                dtend = datetime.combine(subject.get("day_end"), end_time, tzinfo=tz)

                event = iEvent()
                # SUMMARY
                event.add("summary", vText(subject.get("name", "")))
                # DTSTART/DTEND jako aware datetime (zostaną zapisane jako "TZID=Europe/Warsaw")
                event.add("dtstart", dtstart)
                event.add("dtend", dtend)
                # UID
                uid_val = subject.get("uid")
                event.add("uid", uid_val)
                # Opcjonalnie stempel czasu
                event.add("dtstamp", datetime.now(tz))

                # GEO LOCATION
                location = subject.get("location")
                lat = subject.get("lat")
                lng = subject.get("lng")
                if all([location, lat, lng]):
                    event.add("location", vText(f"{location}"))
                    event.add(
                        "X-APPLE-STRUCTURED-LOCATION",
                        f"geo:{lat},{lng}",
                        parameters={
                            "VALUE": "URI",
                            "X-ADDRESS": f"{location}",
                            "X-APPLE-RADIUS": "72",
                            "X-TITLE": f"{location}",
                        },
                    )

                calendar.add_component(event)

        ical_bytes = calendar.to_ical()
        ical_text = ical_bytes.decode("utf-8")
        return ical_text


calendar_serializers_mapper = {"ics": ICSService, "icalendar": ICalendarService}


class ScheduleService:

    @staticmethod
    async def delete_and_create_schedules(schedules: List[ScheduleCreate], db: AsyncSession):
        async with db.begin():
            await db.execute(delete(Schedule))
            await db.execute(insert(Schedule), [s.model_dump() for s in schedules])

    @staticmethod
    def _prepare_data(schedules: List[Schedule]):
        result: List[Dict] = []

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
                    current_range = {
                        "start": start,
                        "end": end,
                        "name": subject.get("name", ""),
                        "uid": subject.get("uid", ""),
                        "location": subject.get("location"),
                        "lat": subject.get("lat"),
                        "lng": subject.get("lng"),
                        "day_begin": schedule.date,
                        "day_end": schedule.date,
                    }
                elif current_range["name"] == subject.get("name", "") and (
                    current_range["end"] == start
                    or (
                        (datetime.strptime(current_range["end"], "%H:%M") + timedelta(minutes=5)).time()
                        == datetime.strptime(start, "%H:%M").time()
                    )
                ):
                    current_range["end"] = end
                else:
                    aggregated.append(
                        (
                            f"{current_range['start']}-{current_range['end']}",
                            dict(
                                name=current_range["name"],
                                uid=current_range["uid"],
                                location=current_range["location"],
                                lat=current_range["lat"],
                                lng=current_range["lng"],
                                day_begin=current_range["day_begin"],
                                day_end=current_range["day_end"],
                            ),
                        )
                    )
                    current_range = {
                        "start": start,
                        "end": end,
                        "name": subject.get("name", ""),
                        "uid": subject.get("uid", ""),
                        "location": subject.get("location"),
                        "lat": subject.get("lat"),
                        "lng": subject.get("lng"),
                        "day_begin": schedule.date,
                        "day_end": schedule.date,
                    }

            if current_range:
                aggregated.append(
                    (
                        f"{current_range['start']}-{current_range['end']}",
                        dict(
                            name=current_range["name"],
                            uid=current_range["uid"],
                            location=current_range["location"],
                            lat=current_range["lat"],
                            lng=current_range["lng"],
                            day_begin=current_range["day_begin"],
                            day_end=current_range["day_end"],
                        ),
                    )
                )

            result.append(dict(aggregated))

        return result

    def create_calendar(self, schedules: List[Schedule], calendar_package: str) -> str:
        data = self._prepare_data(schedules)
        return calendar_serializers_mapper[calendar_package].serialize_calendar(data)


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
