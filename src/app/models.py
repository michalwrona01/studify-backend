from sqlalchemy import JSON, Boolean, Date, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models import BaseModel


class User(BaseModel):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    calendar_token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    section: Mapped[str | None] = mapped_column(String(15), nullable=True)
    session_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ScheduleFile(BaseModel):
    __tablename__ = "schedules_files"

    md5_hash: Mapped[str] = mapped_column(String(32), nullable=False)


class Schedule(BaseModel):
    __tablename__ = "schedules"

    date = mapped_column(Date, nullable=False)
    day_of_week: Mapped[str] = mapped_column(String(31), nullable=False)
    group: Mapped[str] = mapped_column(String(15), nullable=False)
    section: Mapped[str] = mapped_column(String(15), nullable=False)
    mode: Mapped[str] = mapped_column(String(15), nullable=False)
    hours: Mapped[JSON] = mapped_column(JSON, nullable=False)
