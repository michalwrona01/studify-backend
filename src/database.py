import importlib.util
import os
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import exc
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.pool import NullPool

from src.config import database_settings

SQLALCHEMY_DATABASE_URL = f"postgresql+asyncpg://{database_settings.DATABASE_USER}:{database_settings.DATABASE_PASSWORD}@{database_settings.DATABASE_HOST}:{database_settings.DATABASE_PORT}/{database_settings.DATABASE_DB}"

Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, echo=True, poolclass=NullPool)
    async_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except exc.SQLAlchemyError as error:
            await session.rollback()
            raise


def import_all_models() -> None:
    """Used by extra tools (such as alembic) to detect all models"""

    base_dir = Path(__file__).resolve().parent
    for root, dirs, files in os.walk(base_dir):
        if "models.py" in files:
            module_path = os.path.join(root, "models.py")
            module_name = os.path.relpath(module_path, base_dir).replace(os.sep, ".").replace(".py", "")
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
