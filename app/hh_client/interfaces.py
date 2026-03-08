from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from playwright.async_api import BrowserContext


class IVacancyListParser(ABC):
    @abstractmethod
    async def fetch_ids(self, url: str, max_pages: int, context: BrowserContext) -> List[Dict[str, str]]:
        pass


class IVacancyDetailParser(ABC):
    @abstractmethod
    async def parse(self, url: str, context: BrowserContext) -> Dict[str, Any]:
        pass


class IVacancyFilter(ABC):
    @abstractmethod
    def is_match(self, title: str) -> bool:
        pass


class IVacancyRepository(ABC):
    @abstractmethod
    async def save_if_not_exists(self, vacancy: dict) -> bool:
        pass


class IResponseService(ABC):
    @abstractmethod
    async def respond_to_vacancy(
            self,
            vacancy_url: str,
            resume_id: str,
            message: str,
            context: BrowserContext
    ) -> bool:
        """Откликнуться на вакансию. Возвращает True при успехе."""
        pass
