import asyncio
import secrets

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from src.app.dependencies import get_session_user
from src.app.models import User
from src.app.security import DUMMY_PASSWORD_HASH, MAX_PASSWORD_LENGTH, verify_password
from src.database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def csrf_token(request: Request) -> str:
    if "csrf_token" not in request.session:
        request.session["csrf_token"] = secrets.token_urlsafe(32)
    return request.session["csrf_token"]


def validate_csrf(request: Request, token: str) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not secrets.compare_digest(expected.encode(), token.encode()):
        raise HTTPException(status_code=403, detail="Nieprawidłowy formularz. Odśwież stronę.")


@router.get("/login")
async def login_page(request: Request, user: User | None = Depends(get_session_user)):
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={"csrf_token": csrf_token(request)})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(max_length=150),
    password: str = Form(max_length=MAX_PASSWORD_LENGTH),
    csrf: str = Form(default="", max_length=128),
    db: AsyncSession = Depends(get_db),
):
    validate_csrf(request, csrf)
    user = await db.scalar(select(User).where(User.username == username.strip()))
    valid_password = await asyncio.to_thread(
        verify_password, password, user.password_hash if user else DUMMY_PASSWORD_HASH
    )
    if not valid_password or user is None:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"csrf_token": csrf_token(request), "error": "Nieprawidłowa nazwa użytkownika lub hasło."},
            status_code=401,
        )
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["version"] = user.session_version
    csrf_token(request)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request, csrf: str = Form(default="", max_length=128)):
    validate_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
