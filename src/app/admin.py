import asyncio
import secrets

from aiosmtplib.errors import SMTPException
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi_mail import MessageType
from fastapi_mail.errors import ConnectionErrors
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from src.app.auth import csrf_token, validate_csrf
from src.app.config import smtp_config
from src.app.dependencies import require_admin
from src.app.models import User
from src.app.security import hash_password
from src.app.services import SMTPService
from src.app.user_management import available_sections, validate_email_preferences, validate_user_fields
from src.database import get_db
from src.config import settings

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="templates")


async def mail_form(request, db, admin, values=None, error=None, status_code=200):
    addresses = (
        await db.scalars(
            select(User.email).where(User.email.is_not(None), User.email != "").distinct().order_by(User.email)
        )
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="admin_mail.html",
        context={
            "user": admin,
            "addresses": addresses,
            "values": values or {},
            "csrf_token": csrf_token(request),
            "error": error,
            "sent": request.session.pop("admin_mail_sent", False),
            "mail_enabled": settings.IS_EMAILS_SEND,
        },
        status_code=status_code,
    )


@router.get("/mail")
async def mail_page(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await mail_form(request, db, admin)


@router.post("/mail")
async def send_admin_mail(
    request: Request,
    recipients: list[str] = Form(default=[]),
    subject: str = Form(default="", max_length=200),
    body: str = Form(default="", max_length=20000),
    csrf: str = Form(default="", max_length=128),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    validate_csrf(request, csrf)
    values = {"recipients": recipients, "subject": subject, "body": body}
    try:
        if not settings.IS_EMAILS_SEND:
            raise ValueError("Wysyłanie e-maili jest wyłączone w konfiguracji aplikacji.")
        if not subject.strip() or not body.strip():
            raise ValueError("Podaj temat i treść wiadomości.")
        if "\r" in subject or "\n" in subject:
            raise ValueError("Temat musi mieścić się w jednym wierszu.")
        selected = set(recipients)
        if not selected or len(selected) > 500:
            raise ValueError("Wybierz od 1 do 500 adresów e-mail.")
        available = set(
            (await db.scalars(select(User.email).where(User.email.in_(selected)))).all()
        )
        if selected != available:
            raise ValueError("Lista adresów zmieniła się. Wybierz ponownie odbiorców.")
        addresses = sorted({validate_email_preferences(email, True) for email in selected})
    except ValueError as error:
        return await mail_form(request, db, admin, values, str(error), 400)
    try:
        await SMTPService(config=smtp_config).send_mail(
            recipients=[],
            bcc=addresses,
            subject=subject.strip(),
            body=body.strip(),
            subtype=MessageType.plain,
        )
    except (SMTPException, ConnectionErrors, OSError):
        return await mail_form(
            request, db, admin, values,
            "Nie udało się potwierdzić wysyłki. Sprawdź konfigurację SMTP i status dostarczenia "
            "przed ponowieniem — część odbiorców mogła już otrzymać wiadomość.",
            502,
        )
    request.session["admin_mail_sent"] = True
    return RedirectResponse("/admin/mail", status_code=303)


def admin_identity(admin: User) -> dict:
    return {"username": admin.username, "is_admin": True}


async def user_or_404(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono użytkownika.")
    return user


async def user_form(request, db, admin, values, user_id=None, error=None):
    return templates.TemplateResponse(
        request=request,
        name="admin_user.html",
        context={
            "user": admin,
            "values": values,
            "editing_id": user_id,
            "sections": await available_sections(db),
            "error": error,
            "csrf_token": csrf_token(request),
        },
        status_code=400 if error else 200,
    )


@router.get("")
async def user_list(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    users = (await db.scalars(select(User).order_by(User.username))).all()
    return templates.TemplateResponse(
        request=request,
        name="admin_users.html",
        context={"user": admin, "users": users, "csrf_token": csrf_token(request)},
    )


@router.get("/users/new")
async def new_user_page(request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    return await user_form(request, db, admin_identity(admin), {})


@router.get("/users/{user_id}/edit")
async def edit_user_page(
    user_id: int, request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    target = await user_or_404(db, user_id)
    values = {
        "username": target.username,
        "section": target.section,
        "is_admin": target.is_admin,
        "email": target.email or "",
        "email_notifications": target.email_notifications,
    }
    return await user_form(request, db, admin_identity(admin), values, user_id)


@router.post("/users/new")
async def create_user(
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    section: str = Form(default=""),
    is_admin: bool = Form(default=False),
    email: str = Form(default=""),
    email_notifications: bool = Form(default=False),
    csrf: str = Form(default=""),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    validate_csrf(request, csrf)
    identity = admin_identity(admin)
    values = {
        "username": username.strip(),
        "section": section,
        "is_admin": is_admin,
        "email": email,
        "email_notifications": email_notifications,
    }
    try:
        await validate_user_fields(db, values["username"], section)
        values["email"] = validate_email_preferences(email, email_notifications)
        password_hash = await asyncio.to_thread(hash_password, password)
        db.add(User(**values, password_hash=password_hash, calendar_token=secrets.token_urlsafe(32)))
        await db.commit()
    except ValueError as error:
        return await user_form(request, db, identity, values, error=str(error))
    except IntegrityError:
        await db.rollback()
        return await user_form(request, db, identity, values, error="Użytkownik o tej nazwie już istnieje.")
    return RedirectResponse("/admin", status_code=303)


@router.post("/users/{user_id}/edit")
async def edit_user(
    user_id: int,
    request: Request,
    username: str = Form(default=""),
    password: str = Form(default=""),
    section: str = Form(default=""),
    is_admin: bool = Form(default=False),
    email: str = Form(default=""),
    email_notifications: bool = Form(default=False),
    csrf: str = Form(default=""),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    validate_csrf(request, csrf)
    target = await user_or_404(db, user_id)
    identity, own_account = admin_identity(admin), admin.id == user_id
    values = {
        "username": username.strip(),
        "section": section,
        "is_admin": is_admin,
        "email": email,
        "email_notifications": email_notifications,
    }
    try:
        await validate_user_fields(db, values["username"], section)
        values["email"] = validate_email_preferences(email, email_notifications)
        if own_account and not is_admin:
            raise ValueError("Nie możesz odebrać sobie uprawnień administratora.")
        password_hash = await asyncio.to_thread(hash_password, password) if password else None
        target.username, target.section, target.is_admin = values["username"], section, is_admin
        target.email, target.email_notifications = values["email"], email_notifications
        if password_hash:
            target.password_hash = password_hash
            target.session_version += 1
        version = target.session_version
        await db.commit()
        if own_account:
            request.session["version"] = version
    except ValueError as error:
        return await user_form(request, db, identity, values, user_id, str(error))
    except IntegrityError:
        await db.rollback()
        return await user_form(request, db, identity, values, user_id, "Użytkownik o tej nazwie już istnieje.")
    return RedirectResponse("/admin", status_code=303)


@router.get("/users/{user_id}/delete")
async def delete_user_page(
    user_id: int, request: Request, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)
):
    target = await user_or_404(db, user_id)
    if target.id == admin.id:
        raise HTTPException(status_code=400, detail="Nie możesz usunąć swojego konta.")
    return templates.TemplateResponse(
        request=request,
        name="admin_delete.html",
        context={"user": admin, "target": target, "csrf_token": csrf_token(request)},
    )


@router.post("/users/{user_id}/delete")
async def delete_user(
    user_id: int,
    request: Request,
    csrf: str = Form(default=""),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    validate_csrf(request, csrf)
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Nie możesz usunąć swojego konta.")
    target = await user_or_404(db, user_id)
    await db.delete(target)
    await db.commit()
    return RedirectResponse("/admin", status_code=303)
