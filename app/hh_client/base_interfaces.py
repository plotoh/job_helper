from abc import ABC, abstractmethod


class IVacancyFilter(ABC):
    @abstractmethod
    def is_match(self, title: str) -> bool:
        pass


class IVacancyRepository(ABC):
    @abstractmethod
    async def save_if_not_exists(self, vacancy: dict) -> bool:
        pass
