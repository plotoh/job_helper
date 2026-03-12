from typing import Optional, List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import PostgresDsn, computed_field, Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )

    # База данных (оставляем, если нужна для других целей)
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "hhparser"
    DB_USER: str = "user"
    DB_PASSWORD: str = "pass"
    DB_DSN: Optional[PostgresDsn] = None

    # Параметры парсинга
    HH_SEARCH_URL: str = "https://hh.ru/search/vacancy?text=Python+Developer&area=1"
    MAX_PAGES: int = 2  # not actual
    MAX_CONCURRENT: int = 5
    PAGES_PER_WINDOW: int = 3

    # Cookies и шаблон
    HH_COOKIES_FILE: str = "../cookies/cookies.txt"
    MESSAGE_TEMPLATE_FILE: str = "app/message_template.txt"
    RESUME_ID: str = ""
    TG_USERNAME: str = 'test_tg_username'
    PARSING_COOKIES_FILE: str = "cookies/parsing.txt"
    COOKIES_DIR: str = "cookies"

    # Playwright
    PLAYWRIGHT_HEADLESS: bool = True

    # Telegram bot settings
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_ADMIN_ID: int = 0
    TELEGRAM_USERS_IDS: str = ""

    # Параметры рабочего дня и пауз
    WORK_START_HOUR: int = 10
    WORK_START_MINUTE: int = 0
    WORK_END_HOUR: int = 23
    WORK_END_MINUTE: int = 0
    PAUSE_COUNT: int = 3
    PAUSE_MIN_MINUTES: int = 30
    PAUSE_MAX_MINUTES: int = 60
    MIN_GAP_MINUTES: int = 10
    DAILY_LIMIT_MIN: int = 53
    DAILY_LIMIT_MAX: int = 137

    # Прокси (опционально)
    # PROXY_SERVER: Optional[str] = None  # например "http://proxy.example.com:8080"
    # PROXY_USERNAME: Optional[str] = None
    # PROXY_PASSWORD: Optional[str] = None

    # Логирование
    LOG_LEVEL: str = "INFO"

    @computed_field
    @property
    def database_dsn(self) -> PostgresDsn:
        if self.DB_DSN:
            return self.DB_DSN
        return PostgresDsn.build(
            scheme="postgresql+asyncpg",
            username=self.DB_USER,
            password=self.DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            path=self.DB_NAME,
        )


settings = Settings()
