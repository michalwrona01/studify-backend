"""Administrator-only mailbox UI. Never logs message contents or server errors."""

import asyncio
import logging
from email import policy
from urllib.parse import quote, urlencode

from aiosmtplib.errors import SMTPException
from fastapi import APIRouter, Depends, Form, Query, Request, UploadFile, File
from fastapi.responses import RedirectResponse, Response
from fastapi_mail.connection import Connection
from fastapi_mail.errors import ConnectionErrors
from imapclient.exceptions import IMAPClientError
from starlette.templating import Jinja2Templates

from src.app.auth import csrf_token, validate_csrf
from src.app.config import mailbox_config, smtp_config
from src.app.dependencies import require_admin
from src.app.mailbox_service import MailboxError, MailboxService, compose_message
from src.app.models import User
from src.config import settings

router = APIRouter(prefix="/admin/poczta", dependencies=[Depends(require_admin)])
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)
slots = asyncio.Semaphore(4)
MAIL_ERRORS = (IMAPClientError, OSError, ValueError)


async def run_mail(operation, *args):
    async with slots:
        try:
            return await asyncio.to_thread(operation, *args)
        except MailboxError:
            raise
        except MAIL_ERRORS as error:
            logger.warning("Mailbox operation failed: %s", type(error).__name__)
            raise MailboxError(
                "Nie udało się połączyć ze skrzynką lub wykonać operacji. Sprawdź konfigurację IMAP i spróbuj ponownie."
            ) from None


def render(request, admin, template, status_code=200, **context):
    return templates.TemplateResponse(
        request=request,
        name=template,
        status_code=status_code,
        context={
            "user": admin,
            "csrf_token": csrf_token(request),
            "mail_address": str(smtp_config.MAIL_FROM),
            "configured": bool(mailbox_config.IMAP_HOST),
            "mail_enabled": settings.IS_EMAILS_SEND,
            "notice": request.session.pop("mailbox_notice", None),
            **context,
        },
    )


def redirect(folder="INBOX"):
    return RedirectResponse("/admin/poczta?" + urlencode({"folder": folder}), status_code=303)


@router.get("")
async def inbox(
    request: Request,
    admin: User = Depends(require_admin),
    folder: str = Query(default="INBOX", max_length=512),
    page: int = Query(default=1, ge=1),
    q: str = Query(default="", max_length=200),
):
    data = dict(folders=[], messages=[], validity=0, page=1, pages=1, total=0, unread=0)
    error = None
    if mailbox_config.IMAP_HOST:
        try:
            data = await run_mail(MailboxService().listing, folder, page, q)
        except MailboxError as exc:
            error = str(exc)
    return render(request, admin, "mailbox.html", folder=folder, q=q, error=error, **data)


@router.get("/message")
async def message(
    request: Request,
    admin: User = Depends(require_admin),
    folder: str = Query(max_length=512),
    uid: int = Query(gt=0),
    validity: int = Query(gt=0),
):
    try:
        data = await run_mail(MailboxService().read, folder, uid, validity)
        return render(request, admin, "mailbox_message.html", folder=folder, uid=uid, validity=validity, **data)
    except MailboxError as exc:
        request.session["mailbox_notice"] = str(exc)
        return redirect(folder)


@router.get("/attachment")
async def attachment(
    folder: str = Query(max_length=512), uid: int = Query(gt=0), validity: int = Query(gt=0), part: int = Query(ge=0)
):
    try:
        filename, content = await run_mail(MailboxService().attachment, folder, uid, validity, part)
        return Response(
            content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )
    except MailboxError as exc:
        return Response(str(exc), status_code=400, media_type="text/plain")


@router.post("/action")
async def action(
    request: Request,
    csrf: str = Form(default="", max_length=128),
    folder: str = Form(max_length=512),
    uid: int = Form(gt=0),
    validity: int = Form(gt=0),
    action: str = Form(max_length=20),
    destination: str = Form(default="", max_length=512),
):
    validate_csrf(request, csrf)
    try:
        await run_mail(MailboxService().action, folder, uid, validity, action, destination)
        request.session["mailbox_notice"] = "Zapisano zmianę w skrzynce."
    except MailboxError as exc:
        request.session["mailbox_notice"] = str(exc)
    return redirect(folder)


@router.get("/compose")
async def compose(
    request: Request,
    admin: User = Depends(require_admin),
    folder: str = Query(default="INBOX", max_length=512),
    uid: int | None = Query(default=None, gt=0),
    validity: int | None = Query(default=None, gt=0),
    mode: str = Query(default="new", pattern="^(new|reply|forward|draft)$"),
):
    values, error = {}, None
    if uid and validity and mode != "new":
        try:
            original = (await run_mail(MailboxService().read, folder, uid, validity))["message"]
            prefix = {"reply": "Re: ", "forward": "Fwd: ", "draft": ""}[mode]
            values = {
                "to": original["reply_to"] if mode == "reply" else original["to"] if mode == "draft" else "",
                "cc": original["cc"] if mode == "draft" else "",
                "bcc": original["bcc"] if mode == "draft" else "",
                "subject": prefix + original["subject"],
                "body": (
                    original["text"]
                    if mode == "draft"
                    else "\n\n--- " + original["date"] + ", " + original["sender"] + " ---\n" + original["text"]
                ),
            }
            if original["attachments"]:
                error = "Załączniki oryginału nie są dołączane automatycznie. Pobierz je i dodaj do formularza, jeśli chcesz je przesłać."
        except MailboxError as exc:
            error = str(exc)
    return render(
        request,
        admin,
        "mailbox_compose.html",
        values=values,
        error=error,
        folder=folder,
        uid=uid or "",
        validity=validity or "",
        mode=mode,
    )


@router.post("/compose")
async def send(
    request: Request,
    admin: User = Depends(require_admin),
    csrf: str = Form(default="", max_length=128),
    to: str = Form(default="", max_length=4000),
    cc: str = Form(default="", max_length=4000),
    bcc: str = Form(default="", max_length=4000),
    subject: str = Form(default="", max_length=200),
    body: str = Form(default="", max_length=100000),
    operation: str = Form(default="send"),
    folder: str = Form(default="INBOX", max_length=512),
    uid: str = Form(default="", max_length=20),
    validity: str = Form(default="", max_length=20),
    mode: str = Form(default="new", max_length=20),
    attachments: list[UploadFile] = File(default=[]),
):
    validate_csrf(request, csrf)
    values = {"to": to, "cc": cc, "bcc": bcc, "subject": subject, "body": body}
    service = MailboxService()
    error = None
    files = []
    total = 0
    try:
        if operation not in {"send", "draft"}:
            raise MailboxError("Nieznana operacja.")
        if not mailbox_config.IMAP_HOST:
            raise MailboxError("Skonfiguruj IMAP_HOST przed korzystaniem ze skrzynki.")
        if len(attachments) > 10:
            raise MailboxError("Możesz dodać maksymalnie 10 załączników.")
        for upload in attachments:
            if not upload.filename:
                continue
            content = await upload.read(10 * 1024 * 1024 - total + 1)
            total += len(content)
            if total > 10 * 1024 * 1024:
                raise MailboxError("Załączniki mogą mieć łącznie do 10 MB.")
            files.append((upload.filename, content))
        original = None
        if mode == "reply" and uid and validity:
            original = (await run_mail(service.read, folder, int(uid), int(validity)))["message"]
        mail, envelope = compose_message(to, cc, bcc, subject, body, files, original, draft=operation == "draft")
        raw = mail.as_bytes(policy=policy.SMTP)
        if operation == "draft":
            await run_mail(service.append, "drafts", raw)
            request.session["mailbox_notice"] = (
                "Zapisano nowy szkic. Poprzednia wersja, jeśli istnieje, pozostała w skrzynce."
            )
            return redirect(folder)
        if not settings.IS_EMAILS_SEND:
            raise MailboxError("Wysyłanie e-maili jest wyłączone w konfiguracji aplikacji.")
        if mailbox_config.IMAP_SAVE_SENT:
            await run_mail(service.check_sent_folder)
        if "Bcc" in mail:
            del mail["Bcc"]
        try:
            async with Connection(smtp_config) as connection:
                if smtp_config.SUPPRESS_SEND:
                    raise MailboxError("Wysyłanie SMTP jest wyłączone w konfiguracji.")
                refused, _ = await connection.session.send_message(
                    mail, sender=str(smtp_config.MAIL_FROM), recipients=envelope
                )
        except (SMTPException, ConnectionErrors, OSError) as exc:
            logger.warning("Mailbox SMTP failed: %s", type(exc).__name__)
            raise MailboxError(
                "Nie udało się potwierdzić wysyłki. Sprawdź dostarczenie przed ponowieniem — wiadomość mogła zostać wysłana."
            ) from None
        notice = (
            "Serwer SMTP przyjął wiadomość."
            if not refused
            else "Serwer przyjął wiadomość tylko dla części odbiorców. Sprawdź dostarczenie przed ponowieniem."
        )
        if mailbox_config.IMAP_SAVE_SENT:
            try:
                await run_mail(service.append, "sent", raw)
            except MailboxError:
                notice += " Nie udało się zapisać kopii w Wysłanych. Nie wysyłaj wiadomości ponownie."
        request.session["mailbox_notice"] = notice
        return redirect(folder)
    except (MailboxError, ValueError) as exc:
        error = (
            str(exc)
            if isinstance(exc, MailboxError)
            else "Nieprawidłowe odwołanie do wiadomości. Otwórz formularz ponownie."
        )
    finally:
        for upload in attachments:
            await upload.close()
    if files:
        error += " Ponownie wybierz załączniki przed ponowieniem operacji."
    return render(
        request,
        admin,
        "mailbox_compose.html",
        status_code=400,
        values=values,
        error=error,
        folder=folder,
        uid=uid,
        validity=validity,
        mode=mode,
    )
