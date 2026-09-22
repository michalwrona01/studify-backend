from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import EmailStr, TypeAdapter, ValidationError

from src.app.models import Schedule


def validate_email_preferences(email: str | None, email_notifications: bool) -> str | None:
    email = (email or "").strip()
    if not email:
        if email_notifications:
            raise ValueError("Podaj adres e-mail, aby włączyć powiadomienia.")
        return None
    try:
        normalized = str(TypeAdapter(EmailStr).validate_python(email))
    except ValidationError:
        raise ValueError("Podaj poprawny adres e-mail.") from None
    if len(normalized) > 254:
        raise ValueError("Adres e-mail może mieć maksymalnie 254 znaki.")
    return normalized


async def available_sections(db: AsyncSession) -> list[str]:
    sections = (await db.scalars(select(Schedule.section).distinct())).all()
    return sorted(sections, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value))


async def validate_user_fields(db: AsyncSession, username: str, section: str) -> None:
    if not username.strip() or len(username) > 150:
        raise ValueError("Nazwa użytkownika musi mieć od 1 do 150 znaków.")
    if not section or len(section) > 15 or section not in await available_sections(db):
        raise ValueError("Wybierz jedną z dostępnych sekcji.")
