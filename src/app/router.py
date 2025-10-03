import json
from hashlib import md5
from typing import List

from fastapi import APIRouter, Depends, Response, UploadFile, File
from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models import Schedule
from src.app.schemas import ScheduleCreate, ScheduleResponse
from src.app.selectors import ScheduleSelector
from src.app.services import ScheduleService, ScheduleFileService
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
    selector = ScheduleSelector(db=db)

    schedules = await selector.get_by_section(section=section, order_by=Schedule.date.asc())
    schedules_list = [ScheduleResponse.model_validate(s).model_dump() for s in schedules]
    etag = md5(str(schedules_list).encode()).hexdigest()

    calendar = ScheduleService.create_calendar(schedules=schedules)
    serialized_data = ScheduleService.serialize_calendar(calendar=calendar)

    last_modified = await selector.get_last_modified_by_section(section=section)

    serialized_data = ""
    
    return Response(
        content=serialized_data,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=plan_zajec_lekarski_as_sekcja_{section}.ics",
            "ETag": etag,
            "Last-Modified": last_modified.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "Cache-Control": "no-cache",
        },
    )
