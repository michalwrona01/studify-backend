from datetime import datetime
from typing import List
from zoneinfo import ZoneInfo

from ics import Calendar, Event
from ics.component import Component

from src.app.models import Schedule


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
