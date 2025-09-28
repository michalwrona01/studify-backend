from typing import List

from fastapi import APIRouter, Depends, Response
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models import Schedule
from src.app.schemas import ScheduleCreate
from src.app.selectors import ScheduleSelector
from src.app.services import ScheduleService
from src.database import get_db

router = APIRouter(prefix="/api")


@router.post("/schedules", response_model=ScheduleCreate)
async def schedule_create(schedules: List[ScheduleCreate], db: AsyncSession = Depends(get_db)):
    async with db.begin():
        await db.execute(delete(Schedule))
        await db.execute(insert(Schedule), [s.model_dump() for s in schedules])
    return schedules[0]


@router.get("/plan_zajec_lekarski_as.ics")
async def ical_export(section: int = 1, db: AsyncSession = Depends(get_db)):
    schedules = await ScheduleSelector(db=db).get_by_section(section=section, order_by=Schedule.date.asc())
    calendar = ScheduleService.create_calendar(schedules=schedules)
    serialized_data = ScheduleService.serialize_calendar(calendar=calendar)

    return Response(
        content=serialized_data,
        media_type="text/calendar",
        headers={"Content-Disposition": f"attachment; filename=plan_zajec_lekarski_as_sekcja_{section}.ics"},
    )
