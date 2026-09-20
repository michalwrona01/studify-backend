from datetime import date
from typing import Any, Dict

from pydantic import BaseModel, field_validator


class ScheduleCreate(BaseModel):
    date: date
    day_of_week: str
    group: str
    section: str | int
    mode: str
    hours: Dict[str, Any] = {}

    class Config:
        from_attributes = True

    @field_validator("section", mode="before")
    @classmethod
    def convert_section_to_str(cls, value):
        return str(value)



class ScheduleResponse(ScheduleCreate):
    id: int
