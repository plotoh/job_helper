from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from playwright.async_api import BrowserContext


class IVacancyListParser(ABC):
    @abstractmethod
    async def fetch_ids(self, url: str, page: int, context, order_by_publication: bool = False) -> List[Dict[str, str]]:
        pass


class IVacancyDetailParser(ABC):
    @abstractmethod
    async def parse(self, url: str, context: BrowserContext) -> Dict[str, Any]:
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
