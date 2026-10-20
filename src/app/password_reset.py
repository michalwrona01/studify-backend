import asyncio
import hashlib
import json
import logging
import secrets
import socket
import ssl
import time

from aiosmtplib.errors import SMTPAuthenticationError, SMTPException, SMTPResponseException
from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi_mail import MessageType
from fastapi_mail.errors import ConnectionErrors
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from src.app.auth import csrf_token, validate_csrf
from src.app.config import smtp_config
from src.app.models import User
from src.app.security import MAX_PASSWORD_LENGTH, hash_password
from src.app.services import SMTPService
from src.config import settings
from src.database import get_db

router = APIRouter()
templates = Jinja2Templates(directory="templates")
TOKEN_LIFETIME = 30 * 60
REQUEST_COOLDOWN = 60
INVALID_LINK = "Link jest nieprawidłowy, wygasł lub został już użyty. Poproś o nowy link."
logger = logging.getLogger(__name__)


def recovery_configuration_errors() -> list[str]:
    errors = []
    if not settings.IS_EMAILS_SEND:
        errors.append("Wysyłanie e-maili jest wyłączone. Ustaw IS_EMAILS_SEND=1.")
    if not settings.PUBLIC_BASE_URL:
        errors.append("Brakuje adresu aplikacji. Ustaw PUBLIC_BASE_URL, np. https://as.michalwrona.pl.")
    return errors


def log_recovery_mail_error(error: Exception) -> None:
    # FastAPI-Mail wraps connection failures in ConnectionErrors. Inspect the
    # original exception, but never log its message (it may contain credentials,
    # recipient addresses or provider diagnostics echoing submitted data).
    cause = error
    seen = set()
    while id(cause) not in seen:
        seen.add(id(cause))
        nested = cause.__cause__ or cause.__context__
        if nested is None or id(nested) in seen:
            break
        cause = nested
    if isinstance(cause, SMTPAuthenticationError):
        hint = "Sprawdź MAIL_USERNAME, MAIL_PASSWORD oraz dostęp SMTP na koncie nadawcy."
    elif isinstance(cause, ssl.SSLError):
        hint = "Sprawdź certyfikat serwera oraz MAIL_STARTTLS i MAIL_SSL_TLS."
    elif isinstance(cause, socket.gaierror):
        hint = "Sprawdź MAIL_SERVER oraz rozwiązywanie DNS w kontenerze aplikacji."
    elif isinstance(cause, TimeoutError):
        hint = "Sprawdź dostęp sieciowy do SMTP, firewall i port serwera."
    elif isinstance(cause, ConnectionRefusedError):
        hint = "Serwer odrzucił połączenie. Sprawdź MAIL_SERVER, MAIL_PORT i dostępność SMTP."
    else:
        hint = "Sprawdź konfigurację SMTP i logi serwera pocztowego."
    logger.error(
        "password_recovery.smtp_failed exception=%s cause=%s smtp_code=%s errno=%s "
        "server=%s port=%s starttls=%s ssl_tls=%s. %s",
        type(error).__name__,
        type(cause).__name__,
        cause.code if isinstance(cause, SMTPResponseException) and isinstance(cause.code, int) else None,
        cause.errno if isinstance(cause, OSError) and isinstance(cause.errno, int) else None,
        smtp_config.MAIL_SERVER,
        smtp_config.MAIL_PORT,
        smtp_config.MAIL_STARTTLS,
        smtp_config.MAIL_SSL_TLS,
        hint,
    )


def token_digest(user: User, token: str) -> str:
    # A password, email or session-version change also revokes pending links.
    state = [token, user.password_hash, user.email, user.session_version]
    return hashlib.sha256(json.dumps(state).encode()).hexdigest()


async def send_recovery_mail(email: str, link: str) -> None:
    try:
        await SMTPService(smtp_config).send_mail(
            recipients=[email],
            subject="Ustaw nowe hasło — Studify",
            body=f"Aby ustawić nowe hasło, otwórz link:\n\n{link}\n\n"
            "Link jest jednorazowy i ważny przez 30 minut.\n"
            "Jeśli nie prosisz o zmianę hasła, zignoruj tę wiadomość.",
            subtype=MessageType.plain,
        )
    except (SMTPException, ConnectionErrors, OSError) as error:
        log_recovery_mail_error(error)


@router.get("/forgot-password")
async def forgot_password_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="forgot_password.html", context={"csrf_token": csrf_token(request)}
    )


@router.post("/forgot-password")
async def forgot_password(
    request: Request,
    background_tasks: BackgroundTasks,
    username: str = Form(max_length=150),
    csrf: str = Form(default="", max_length=128),
    db: AsyncSession = Depends(get_db),
):
    validate_csrf(request, csrf)
    configuration_errors = recovery_configuration_errors()
    if configuration_errors:
        explanation = " ".join(configuration_errors)
        logger.error(
            "password_recovery.configuration_unavailable IS_EMAILS_SEND=%s PUBLIC_BASE_URL_configured=%s. "
            "%s Uzupełnij konfigurację środowiska lub Vault backend/app i uruchom ponownie backend.",
            settings.IS_EMAILS_SEND,
            bool(settings.PUBLIC_BASE_URL),
            explanation,
        )
        return templates.TemplateResponse(
            request=request,
            name="forgot_password.html",
            context={
                "csrf_token": csrf_token(request),
                "error": "Odzyskiwanie hasła jest niedostępne z powodu konfiguracji serwera. "
                + explanation
                + " Administrator musi uzupełnić konfigurację środowiska lub Vault backend/app "
                "i uruchomić ponownie backend.",
            },
            status_code=503,
        )
    user = await db.scalar(
        select(User)
        .where(User.username == username.strip())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = int(time.time())
    if (
        user
        and user.email
        and (user.password_reset_sent_at is None or now - user.password_reset_sent_at >= REQUEST_COOLDOWN)
    ):
        token = f"{user.id}.{secrets.token_urlsafe(32)}"
        user.password_reset_hash = token_digest(user, token)
        user.password_reset_expires = now + TOKEN_LIFETIME
        user.password_reset_sent_at = now
        email = user.email
        await db.commit()
        # Use configured origin, never the untrusted request Host header.
        link = f"{str(settings.PUBLIC_BASE_URL).rstrip('/')}/reset-password?token={token}"
        background_tasks.add_task(send_recovery_mail, email, link)
    return templates.TemplateResponse(
        request=request,
        name="forgot_password.html",
        context={"csrf_token": csrf_token(request), "submitted": True},
    )


async def reset_user(db, token: str, lock=False):
    try:
        user_id, secret = token.split(".")
        if len(user_id) > 10 or not user_id.isascii() or not user_id.isdigit() or len(secret) != 43:
            return None
        if not 0 < int(user_id) <= 2_147_483_647:
            return None
        query = select(User).where(User.id == int(user_id)).execution_options(populate_existing=True)
        if lock:
            query = query.with_for_update()
        user = await db.scalar(query)
    except ValueError:
        return None
    if (
        user is None
        or not user.password_reset_hash
        or not user.password_reset_expires
        or user.password_reset_expires <= int(time.time())
        or not secrets.compare_digest(user.password_reset_hash, token_digest(user, token))
    ):
        return None
    return user


def reset_form(request, token="", error=None, invalid=False):
    return templates.TemplateResponse(
        request=request,
        name="reset_password.html",
        context={"csrf_token": csrf_token(request), "token": token, "error": error, "invalid": invalid},
        status_code=400 if error else 200,
    )


@router.get("/reset-password")
async def reset_password_page(
    request: Request, token: str = Query(default="", max_length=128), db: AsyncSession = Depends(get_db)
):
    if await reset_user(db, token) is None:
        return reset_form(request, error=INVALID_LINK, invalid=True)
    # GET does not consume the link, so email scanners cannot invalidate it.
    return reset_form(request, token)


@router.post("/reset-password")
async def reset_password(
    request: Request,
    token: str = Form(default="", max_length=128),
    password: str = Form(default="", max_length=MAX_PASSWORD_LENGTH),
    password_confirmation: str = Form(default="", max_length=MAX_PASSWORD_LENGTH),
    csrf: str = Form(default="", max_length=128),
    db: AsyncSession = Depends(get_db),
):
    validate_csrf(request, csrf)
    user = await reset_user(db, token, lock=True)
    if user is None:
        return reset_form(request, error=INVALID_LINK, invalid=True)
    if password != password_confirmation:
        return reset_form(request, token, "Nowe hasło i jego potwierdzenie muszą być takie same.")
    try:
        password_hash = await asyncio.to_thread(hash_password, password)
    except ValueError as error:
        return reset_form(request, token, str(error))
    user.password_hash = password_hash
    user.session_version += 1
    user.password_reset_hash = None
    user.password_reset_expires = None
    await db.commit()
    request.session.clear()
    request.session["password_reset_done"] = True
    return RedirectResponse("/login", status_code=303)
