from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from src.app.auth import csrf_token, router as auth_router
from src.app.admin import router as admin_router
from src.app.account import router as account_router
from src.app.dependencies import get_session_user
from src.app.models import User
from src.app.router import router as app_router
from src.config import settings
from src.health_check.router import router as health_check_router

app = FastAPI(title="Studify API", debug=settings.DEBUG)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SESSION_SECRET_KEY,
    session_cookie="studify_session",
    max_age=7 * 24 * 60 * 60,
    same_site="lax",
    https_only=settings.SESSION_COOKIE_SECURE,
)


@app.middleware("http")
async def private_page_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/login", "/logout", "/account"} or request.url.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    return response


app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def home_page(
    request: Request,
    user: User | None = Depends(get_session_user),
):
    if user is None:
        return RedirectResponse("/login", status_code=303)
    sections = [user.section] if user.section else []
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "sections": sections,
            "host": request.headers.get("host"),
            "token": user.calendar_token,
            "username": user.username,
            "user": user,
            "csrf_token": csrf_token(request),
        },
    )


app.include_router(health_check_router)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(account_router)
app.include_router(app_router)
