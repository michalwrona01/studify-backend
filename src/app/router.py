from hashlib import md5
from typing import List

from fastapi import APIRouter, Depends, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.config import schedule_mail_config, smtp_config
from src.app.models import Schedule
from src.app.schemas import ScheduleCreate, ScheduleResponse
from src.app.selectors import ScheduleSelector
from src.app.services import ScheduleFileService, ScheduleService, SMTPService
from src.database import get_db

router = APIRouter(prefix="/api")


@router.post("/schedules/files")
async def schedule_file_md5_update_and_send_mail(file: UploadFile, db: AsyncSession = Depends(get_db)):
    is_send_mail = await ScheduleFileService.create_or_update_md5_file(file=file, db=db)
    await file.seek(0)

    if is_send_mail:
        smtp_client = SMTPService(config=smtp_config)
        await smtp_client.send_mail(
            recipients=schedule_mail_config.MAILS_TO.split(","),
            subject=schedule_mail_config.MAIL_SUBJECT,
            body=schedule_mail_config.MAIL_BODY,
            attachments=[file],
        )

    return {"is_email_sent": is_send_mail}


@router.post("/schedules", response_model=List[ScheduleCreate])
async def schedule_create(schedules: List[ScheduleCreate], db: AsyncSession = Depends(get_db)):
    await ScheduleService.delete_and_create_schedules(schedules=schedules, db=db)
    return schedules


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
