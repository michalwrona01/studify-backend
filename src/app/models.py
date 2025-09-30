from sqlalchemy import JSON, Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models import BaseModel


class ScheduleFile(BaseModel):
    __tablename__ = "schedules_files"

    md5_hash: Mapped[str] = mapped_column(String(32), nullable=False)


class Schedule(BaseModel):
    __tablename__ = "schedules"

    date = mapped_column(Date, nullable=False)
    day_of_week: Mapped[str] = mapped_column(String(31), nullable=False)
    group: Mapped[str] = mapped_column(String(15), nullable=False)
    section: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(15), nullable=False)
    hours: Mapped[JSON] = mapped_column(JSON, nullable=False)
