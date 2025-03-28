from pydantic import BaseModel


class ScheduleCreate(BaseModel):
    name: str = None


class ScheduleUpdate(ScheduleCreate):
    id: int
