import asyncio

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from src.app.auth import csrf_token, validate_csrf
from src.app.dependencies import get_session_user
from src.app.models import User
from src.app.security import MAX_PASSWORD_LENGTH, hash_password, verify_password
from src.app.user_management import validate_email_preferences
from src.database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def account_form(request, user, values, error=None):
    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context={
            "user": user,
            "values": values,
            "error": error,
            "csrf_token": csrf_token(request),
            "saved": request.session.pop("account_saved", False),
            "password_changed": request.session.pop("password_changed", False),
        },
        status_code=400 if error else 200,
    )


@router.get("/account")
async def account_page(request: Request, user: User | None = Depends(get_session_user)):
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return account_form(request, user, {"email": user.email or "", "email_notifications": user.email_notifications})


@router.post("/account")
async def update_account(
    request: Request,
    email: str = Form(default=""),
    email_notifications: bool = Form(default=False),
    csrf: str = Form(default=""),
    user: User | None = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
):
    if user is None:
        return RedirectResponse("/login", status_code=303)
    validate_csrf(request, csrf)
    try:
        normalized_email = validate_email_preferences(email, email_notifications)
    except ValueError as error:
        return account_form(request, user, {"email": email, "email_notifications": email_notifications}, str(error))
    # The account always comes from the session, never from a submitted user ID.
    user.email = normalized_email
    user.email_notifications = email_notifications
    await db.commit()
    request.session["account_saved"] = True
    return RedirectResponse("/account", status_code=303)


def password_form(request, user, error=None):
    return templates.TemplateResponse(
        request=request,
        name="password.html",
        context={"user": user, "csrf_token": csrf_token(request), "error": error},
        status_code=400 if error else 200,
    )


@router.get("/account/password")
async def password_page(request: Request, user: User | None = Depends(get_session_user)):
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return password_form(request, user)


@router.post("/account/password")
async def change_password(
    request: Request,
    current_password: str = Form(default="", max_length=MAX_PASSWORD_LENGTH),
    password: str = Form(default="", max_length=MAX_PASSWORD_LENGTH),
    password_confirmation: str = Form(default="", max_length=MAX_PASSWORD_LENGTH),
    csrf: str = Form(default="", max_length=128),
    user: User | None = Depends(get_session_user),
    db: AsyncSession = Depends(get_db),
):
    if user is None:
        return RedirectResponse("/login", status_code=303)
    validate_csrf(request, csrf)
    # Refresh after locking: concurrent changes cannot reuse an old password/session.
    user = await db.scalar(
        select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True)
    )
    if user is None or request.session.get("version") != user.session_version:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)
    if not await asyncio.to_thread(verify_password, current_password, user.password_hash):
        return password_form(request, user, "Obecne hasło jest nieprawidłowe.")
    if password != password_confirmation:
        return password_form(request, user, "Nowe hasło i jego potwierdzenie muszą być takie same.")
    try:
        password_hash = await asyncio.to_thread(hash_password, password)
    except ValueError as error:
        return password_form(request, user, str(error))
    user.password_hash = password_hash
    user.session_version += 1
    user_id, version = user.id, user.session_version
    await db.commit()
    request.session.clear()
    request.session.update(user_id=user_id, version=version, password_changed=True)
    return RedirectResponse("/account", status_code=303)
