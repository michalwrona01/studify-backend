import os

from pydantic_settings import BaseSettings

if bool(int(os.getenv("USE_VALUT", "1"))):
    from vault.vault_settings import *


class DatabaseConfig(BaseSettings):
    DATABASE_HOST: str
    DATABASE_PORT: int
    DATABASE_USER: str
    DATABASE_PASSWORD: str
    DATABASE_DB: str


class Settings(BaseSettings):
    DEBUG: bool


database_settings = DatabaseConfig(
    DATABASE_HOST=os.getenv("DATABASE_HOST"),
    DATABASE_PORT=int(os.getenv("DATABASE_PORT")),
    DATABASE_USER=os.getenv("DATABASE_USER"),
    DATABASE_PASSWORD=os.getenv("DATABASE_PASSWORD"),
    DATABASE_DB=os.getenv("DATABASE_DB"),
)

settings = Settings(DEBUG=bool(int(os.getenv("DEBUG"))))
