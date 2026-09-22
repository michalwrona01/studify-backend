from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from src.app.auth import csrf_token, validate_csrf
from src.app.dependencies import get_session_user
from src.app.models import User
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
