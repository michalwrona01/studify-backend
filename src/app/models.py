from typing import Optional

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models import BaseModel


class Schedule(BaseModel):
    __tablename__ = "schedule"

    name: Mapped[Optional[str]] = mapped_column(String(255))
