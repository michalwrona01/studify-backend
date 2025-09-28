from abc import ABC, abstractmethod


class BaseSelector(ABC):
    @abstractmethod
    def get_all(self):
        pass

    @abstractmethod
    async def get_by_id(self, object_id: int):
        pass
