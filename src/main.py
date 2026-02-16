from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.staticfiles import StaticFiles

from src.app.models import Schedule
from src.app.router import router as app_router
from src.app.selectors import ScheduleSelector
from src.config import settings
from src.database import get_db
from src.health_check.router import router as health_check_router

app = FastAPI(title="Studify API", debug=settings.DEBUG)

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def home_page(request: Request, db: AsyncSession = Depends(get_db)):
    schedules = await ScheduleSelector(db=db).get_distinct_by(Schedule.section)
    sections = [schedule.section for schedule in schedules]
    sections = sorted(sections, key=lambda x: int(''.join(filter(str.isdigit, x))))
    return templates.TemplateResponse(
        request=request, name="index.html", context={"sections": sections, "host": request.headers.get("host")}
    )


app.include_router(health_check_router)
app.include_router(app_router)
