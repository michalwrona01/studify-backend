from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession


class BaseSelector(ABC):
    def __init__(self, *, db: AsyncSession):
        self._db = db

    @abstractmethod
    def get_all(self):
        pass

    @abstractmethod
    async def get_by_id(self, object_id: int):
        pass
