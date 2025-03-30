from fastapi import FastAPI

from src.config import settings
from src.health_check.router import router as health_check

app = FastAPI(title="Studify API", root_path="/api", debug=settings.DEBUG)


app.include_router(health_check)
