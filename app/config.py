from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import PostgresDsn, field_validator, computed_field
import os


class Settings(BaseSettings):
    # База данных
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "hhparser"
    DB_USER: str = "user"
    DB_PASSWORD: str = "pass"

    DB_DSN: Optional[PostgresDsn] = None

    # Параметры парсинга
    HH_SEARCH_URL: str = "https://hh.ru/search/vacancy?text=Python+Developer&area=1"
    MAX_PAGES: int = 2
    MAX_CONCURRENT: int = 5

    HH_COOKIES_FILE: str = "cookies.txt"

    MESSAGE_TEMPLATE_FILE: str = "message_template.txt"

    # Playwright
    PLAYWRIGHT_HEADLESS: bool = True

    # Логирование
    LOG_LEVEL: str = "INFO"

    @computed_field
    @property
    def database_dsn(self) -> PostgresDsn:
        if self.DB_DSN:
            return self.DB_DSN
        # Строим DSN из компонентов
        return PostgresDsn.build(
            scheme="postgresql+asyncpg",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            path=self.DB_NAME,
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )


settings = Settings()