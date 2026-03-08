from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import PostgresDsn, computed_field


class Settings(BaseSettings):
    # База данных (оставляем, если нужна для других целей)
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

    # Cookies и шаблон
    HH_COOKIES_FILE: str = "cookies.txt"
    MESSAGE_TEMPLATE_FILE: str = "app/message_template.txt"
    RESUME_ID: str = ""
    TG_USERNAME: str = ''

    # Playwright
    PLAYWRIGHT_HEADLESS: bool = True

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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )


settings = Settings()