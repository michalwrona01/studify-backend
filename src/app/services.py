import hashlib
import os
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from email.utils import formataddr
from typing import Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from fastapi import UploadFile
from fastapi_mail import ConnectionConfig, MessageSchema, MessageType
from fastapi_mail.connection import Connection
from fastapi_mail.msg import MailMsg
from icalendar import Calendar as iCalendar
from icalendar import Event as iEvent
from icalendar import Alarm as iAlarm
from icalendar import vText
from ics import Calendar as ICSCalendar
from ics import Event as ICSEvent
from pydantic import EmailStr
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models import PreviousSchedule, Schedule, ScheduleFile
from src.app.schemas import ScheduleCreate
from src.app.models_selectors import ScheduleFileSelector


class CalendarBaseService(ABC):
    @staticmethod
    @abstractmethod
    def serialize_calendar(schedules: List[Dict], events_type: str, section: str) -> str:
        pass


class ICSService(CalendarBaseService):
    @staticmethod
    def serialize_calendar(schedules: List[Dict], events_type: str, section: str) -> str:
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
    def serialize_calendar(schedules: List[Dict], events_type: str, section: str) -> str:
        calendar = iCalendar()

        calendar.add("prodid", f"-//{os.getenv("FILE_NAME", "").replace(" ", "//")}//PL")
        calendar.add("version", "2.0")

        match events_type:
            case "inperson":
                calendar.add("name", f"Plan zajęć AŚ - {os.getenv("FILE_NAME")} - Sekcja {section} - Stacjonarne")
                calendar.add("X-WR-CALNAME", f"Plan zajęć AŚ - {os.getenv("FILE_NAME")} - Sekcja {section} - Stacjonarne")
                calendar.color = "blue"
                calendar.add("X-APPLE-CALENDAR-COLOR", "#0000FF")
            case "online":
                calendar.add("name", f"Plan zajęć AŚ - {os.getenv("FILE_NAME")} - Sekcja {section} - Zdalnie")
                calendar.add("X-WR-CALNAME", f"Plan zajęć AŚ - {os.getenv("FILE_NAME")} - Sekcja {section} - Zdalnie")
                calendar.color = "orange"
                calendar.add("X-APPLE-CALENDAR-COLOR", "#FFA500")
            case _:
                pass

        calendar.add("calscale", "GREGORIAN")
        calendar.add("method", "PUBLISH")

        tz = ZoneInfo("Europe/Warsaw")

        for day in schedules:
            for hours, subject in day.items():
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

                # ALARM before 15 mins ""
                if "wykład" in subject.get("name", "").lower():
                    for minutes in [-10, -30]:
                        alarm = iAlarm()
                        alarm.add("action", "DISPLAY")
                        alarm.add("description", f"Przypomnienie: Za {abs(minutes)} minut zacznie się {subject.get("name", "")}")
                        alarm.add("trigger", timedelta(minutes=minutes))
                        event.add_component(alarm)

                subject_name = subject.get("name", "").lower()
                match events_type:
                    case "inperson":
                        if "online" not in subject_name and "Ginekologia i położnictwo (IX-X) dr n. med. Robert Kiełbasiński wykład".lower() not in subject_name:
                            calendar.add_component(event)
                    case "online":
                        if "online" in subject_name or "Ginekologia i położnictwo (IX-X) dr n. med. Robert Kiełbasiński wykład".lower() in subject_name:
                            calendar.add_component(event)
                    case _:
                        pass

        ical_bytes = calendar.to_ical()
        ical_text = ical_bytes.decode("utf-8")
        return ical_text


calendar_serializers_mapper = {"ics": ICSService, "icalendar": ICalendarService}


class ScheduleService:

    @staticmethod
    async def delete_and_create_schedules(schedules: List[ScheduleCreate], db: AsyncSession):
        from src.app.schedule_changes import compare_schedules

        async with db.begin():
            # Serialize imports, including the first one when the tables are empty.
            if db.bind.dialect.name == "postgresql":
                await db.execute(text("LOCK TABLE schedules, schedules_previous, schedules_files IN EXCLUSIVE MODE"))
            previous = list((await db.scalars(select(Schedule))).all())
            changes = compare_schedules(previous, schedules)
            await db.execute(delete(PreviousSchedule))
            fields = list(ScheduleCreate.model_fields) + ["id", "created_at", "modified_at"]
            await db.execute(insert(PreviousSchedule).from_select(fields, select(*(getattr(Schedule, f) for f in fields))))
            await db.execute(delete(Schedule))
            if schedules:
                await db.execute(insert(Schedule), [s.model_dump() for s in schedules])
            schedule_file = (await db.scalars(select(ScheduleFile).limit(1))).first()
            attachment = None
            if schedule_file is not None and schedule_file.content is not None:
                attachment = (schedule_file.filename or "schedule.xls", schedule_file.content)
                # Only the immediately following import may use this upload.
                schedule_file.content = None
        return changes, attachment

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

    def create_calendar(self, schedules: List[Schedule], events_type: str, section: str, calendar_package: str) -> str:
        data = self._prepare_data(schedules)
        return calendar_serializers_mapper[calendar_package].serialize_calendar(data, events_type=events_type, section=section)


class ScheduleFileService:

    @staticmethod
    async def create_or_update_md5_file(*, file: UploadFile, db: AsyncSession) -> bool:
        """Stage the attachment for the next schedule import; report file hash changes."""
        schedule_file_selector = ScheduleFileSelector(db=db)

        content_file = await file.read()
        md5_hash = hashlib.md5(content_file).hexdigest()

        schedule_file = await schedule_file_selector.get_last_schedule_file()

        values = dict(md5_hash=md5_hash, content=content_file, filename=file.filename or "schedule.xls")
        if not schedule_file:
            await db.execute(insert(ScheduleFile).values(**values))
            return True

        changed = schedule_file.md5_hash != md5_hash
        if not changed:
            # Preserve a pending upload, but do not resend an already consumed file.
            values.pop("content")
        await db.execute(update(ScheduleFile).where(ScheduleFile.id == schedule_file.id).values(**values))
        return changed


class SMTPService:
    def __init__(self, config: ConnectionConfig):
        self.config = config

    async def send_mail(
        self,
        recipients: Optional[List[EmailStr]] = None,
        subject: str = "",
        body: Optional[Union[str, list]] = None,
        subtype: MessageType = MessageType.html,
        attachments: Optional[List[Union[UploadFile, Dict, str]]] = None,
        bcc: Optional[List[EmailStr]] = None,
    ) -> bool:
        message = MessageSchema(
            from_name="studify.",
            subject=subject,
            recipients=recipients or [],
            bcc=bcc or [],
            body=body,
            subtype=subtype,
            attachments=attachments or [],
        )
        envelope = list(dict.fromkeys([*message.recipients, *message.bcc]))
        if not envelope:
            raise ValueError("At least one mail recipient is required")
        wire_message = await MailMsg(message)._message(formataddr(("studify.", self.config.MAIL_FROM)))
        if not message.recipients:
            del wire_message["To"]
        # Explicit envelope recipients avoid interpreting an empty To header as
        # an address and never add the sender as an implicit recipient.
        async with Connection(self.config) as connection:
            if not self.config.SUPPRESS_SEND:
                await connection.session.send_message(
                    wire_message, sender=self.config.MAIL_FROM, recipients=envelope
                )
        return True
