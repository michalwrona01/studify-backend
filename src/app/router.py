from hashlib import md5
from typing import List

from fastapi import APIRouter, Depends, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.dependencies import require_calendar_subscription_token
from src.app.models import Schedule
from src.app.schemas import ScheduleCreate, ScheduleResponse
from src.app.models_selectors import ScheduleSelector
from src.app.services import ScheduleFileService, ScheduleService
from src.database import get_db
from src.app.schedule_changes import send_schedule_notifications

router = APIRouter(prefix="/api")


@router.post("/schedules/files")
async def schedule_file_md5_update_and_send_mail(
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
):
    changed = await ScheduleFileService.create_or_update_md5_file(file=file, db=db)
    return {"is_email_sent": False, "is_file_changed": changed}


@router.post("/schedules", response_model=List[ScheduleCreate])
async def schedule_create(schedules: List[ScheduleCreate], db: AsyncSession = Depends(get_db)):
    changes, attachment = await ScheduleService.delete_and_create_schedules(schedules=schedules, db=db)
    await send_schedule_notifications(changes=changes, attachment=attachment, db=db)
    return schedules


@router.get("/plan_zajec_lekarski_as.ics", dependencies=[Depends(require_calendar_subscription_token)])
async def ical_export(section: str = "1", db: AsyncSession = Depends(get_db), events_type: str = ""):
    selector = ScheduleSelector(db=db)

    if not events_type:
        schedules = await selector.get_empty()
    else:
        schedules = await selector.get_by_section(section=section, order_by=Schedule.date.asc())
    schedules_list = [ScheduleResponse.model_validate(s).model_dump() for s in schedules]
    etag = md5(str(schedules_list).encode()).hexdigest()

    calendar_data = ScheduleService().create_calendar(
        schedules=schedules, events_type=events_type, section=section, calendar_package="icalendar"
    )

    last_modified = await selector.get_last_modified_by_section(section=section)

    return Response(
        content=calendar_data,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=plan_zajec_lekarski_as_sekcja_{section}.ics",
            "ETag": etag,
            "Last-Modified": last_modified.strftime("%a, %d %b %Y %H:%M:%S GMT"),
            "Cache-Control": "no-cache",
        },
    )
