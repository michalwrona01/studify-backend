from sqlalchemy import JSON, Boolean, Date, Integer, LargeBinary, String
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
    password_reset_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_reset_expires: Mapped[int | None] = mapped_column(Integer, nullable=True)
    password_reset_sent_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ScheduleFile(BaseModel):
    __tablename__ = "schedules_files"

    md5_hash: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Schedule(BaseModel):
    __tablename__ = "schedules"

    date = mapped_column(Date, nullable=False)
    day_of_week: Mapped[str] = mapped_column(String(31), nullable=False)
    group: Mapped[str] = mapped_column(String(15), nullable=False)
    section: Mapped[str] = mapped_column(String(15), nullable=False)
    mode: Mapped[str] = mapped_column(String(15), nullable=False)
    hours: Mapped[JSON] = mapped_column(JSON, nullable=False)


class PreviousSchedule(BaseModel):
    __tablename__ = "schedules_previous"

    date = mapped_column(Date, nullable=False)
    day_of_week: Mapped[str] = mapped_column(String(31), nullable=False)
    group: Mapped[str] = mapped_column(String(15), nullable=False)
    section: Mapped[str] = mapped_column(String(15), nullable=False)
    mode: Mapped[str] = mapped_column(String(15), nullable=False)
    hours: Mapped[JSON] = mapped_column(JSON, nullable=False)
