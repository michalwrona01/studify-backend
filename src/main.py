from datetime import datetime
from typing import List
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Response, Request
from ics import Calendar, Event
from ics.component import Component
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

from src.app.models import Schedule
from src.app.schemas import ScheduleCreate
from src.config import settings
from src.database import get_db

app = FastAPI(title="Studify API", root_path="/api", debug=settings.DEBUG)

templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def read_item(request: Request):
    return templates.TemplateResponse(
        request=request, name="index.html"
    )

@app.post("/schedules", response_model=ScheduleCreate)
async def schedule_create(
    schedules: List[ScheduleCreate], db: AsyncSession = Depends(get_db)
):
    async with db.begin():
        await db.execute(delete(Schedule))
        await db.execute(insert(Schedule), [s.model_dump() for s in schedules])
    return schedules[0]


@app.get("/plan_zajec_lekarski_as.ics")
async def ical_export(section: int = 1, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Schedule)
        .where(Schedule.section == section)
        .order_by(Schedule.date.asc())
    )
    schedules = result.scalars().all()

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
            event.begin = datetime.combine(
                schedule.date, start_time, tzinfo=ZoneInfo("Europe/Warsaw")
            )
            event.end = datetime.combine(
                schedule.date, end_time, tzinfo=ZoneInfo("Europe/Warsaw")
            )
            event.name = subject
            calendar.events.add(event)

    return Response(
        content=Component.serialize(calendar),
        media_type="text/calendar",
        headers={
            "Content-Disposition": f"attachment; filename=plan_zajec_lekarski_as_sekcja_{section}.ics"
        },
    )
