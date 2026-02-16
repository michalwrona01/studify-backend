from datetime import date
from typing import Any, Dict

from pydantic import BaseModel


class ScheduleCreate(BaseModel):
    date: date
    day_of_week: str
    group: str
    section: str
    mode: str
    hours: Dict[str, Any] = {}

    class Config:
        from_attributes = True


class ScheduleResponse(ScheduleCreate):
    id: int
