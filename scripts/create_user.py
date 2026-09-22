"""Run from the repository root: poetry run python -m scripts.create_user USERNAME --section 1."""

import argparse
import asyncio
from getpass import getpass
import secrets

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.app.models import User
from src.app.security import hash_password
from src.app.user_management import validate_email_preferences, validate_user_fields
from src.database import SQLALCHEMY_DATABASE_URL


async def create_user(
    username: str,
    password_hash: str,
    section: str,
    is_admin: bool = False,
    email: str | None = None,
    email_notifications: bool = False,
) -> None:
    email = validate_email_preferences(email, email_notifications)
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, echo=False, hide_parameters=True)
    try:
        async with async_sessionmaker(engine)() as db:
            await validate_user_fields(db, username, section)
            if await db.scalar(select(User.id).where(User.username == username)) is not None:
                raise ValueError("Użytkownik o tej nazwie już istnieje.")
            db.add(
                User(
                    username=username,
                    password_hash=password_hash,
                    calendar_token=secrets.token_urlsafe(32),
                    section=section,
                    is_admin=is_admin,
                    email=email,
                    email_notifications=email_notifications,
                )
            )
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                raise ValueError("Nie można utworzyć użytkownika: nazwa lub token już istnieje.") from None
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Utwórz konto z unikalnym tokenem kalendarza.")
    parser.add_argument("username", help="Nazwa użytkownika (rozróżnia wielkość liter)")
    parser.add_argument("--section", required=True, help="Jedna sekcja z aktualnego planu zajęć")
    parser.add_argument("--admin", action="store_true", help="Utwórz administratora")
    parser.add_argument("--email", help="Adres e-mail użytkownika")
    parser.add_argument(
        "--email-notifications", action="store_true", help="Włącz powiadomienia na życzenie użytkownika"
    )
    args = parser.parse_args()
    username = args.username.strip()
    if not username or len(username) > 150:
        parser.error("Nazwa użytkownika musi mieć od 1 do 150 znaków.")
    password = getpass("Hasło: ")
    if password != getpass("Powtórz hasło: "):
        parser.error("Hasła nie są identyczne.")
    try:
        asyncio.run(
            create_user(
                username, hash_password(password), args.section, args.admin, args.email, args.email_notifications
            )
        )
    except ValueError as error:
        parser.error(str(error))
    print(f"Utworzono użytkownika {username}. Zaloguj się, aby pobrać linki subskrypcji.")


if __name__ == "__main__":
    main()
