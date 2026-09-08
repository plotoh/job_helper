# app/db/models.py

from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, JSON, Date, Enum, UniqueConstraint, \
    BigInteger, Text
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func
import json
import enum

Base = declarative_base()


class VacancyLevel(str, enum.Enum):
    REGULAR = 'regular'
    JUNIOR = 'JUNIOR'
    SENIOR = 'SENIOR'


class Vacancy(Base):
    __tablename__ = 'vacancies'

    hh_id = Column(String, primary_key=True)  # ID с hh.ru
    title = Column(String, nullable=False)
    url = Column(String, nullable=False)
    salary_text = Column(String, nullable=True)
    experience_min = Column(Integer, nullable=True)
    experience_max = Column(Integer, nullable=True)
    work_format = Column(String, nullable=True)
    skills = Column(JSON, nullable=True)  # список навыков в JSON
    created_at = Column(DateTime, server_default=func.now())
    is_actual = Column(Boolean, nullable=False, server_default='true')  # или default=True
    used_count = Column(Integer, default=0)  # сколько аккаунтов уже откликнулось
    description = Column(Text, nullable=True)  # полное описание вакансии
    score = Column(Integer, default=0)
    level = Column(
        Enum(VacancyLevel, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=VacancyLevel.REGULAR
    )

class Account(Base):
    __tablename__ = 'accounts'

    id = Column(BigInteger, primary_key=True)  ## tg_id
    tg_username = Column(String, unique=True, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    resume_id = Column(String, nullable=False)


class AccountVacancy(Base):
    __tablename__ = 'account_vacancy'

    account_id = Column(BigInteger, ForeignKey('accounts.id'), primary_key=True)
    hh_id = Column(String, ForeignKey('vacancies.hh_id'), primary_key=True)
    responded = Column(Boolean, default=False)
    response_status = Column(String, nullable=True)
    response_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class ResponseError(Base):
    """Ошибки при откликах"""
    __tablename__ = 'response_errors'

    id = Column(Integer, primary_key=True)
    account_id = Column(BigInteger, ForeignKey('accounts.id'), nullable=False)
    vacancy_id = Column(String, ForeignKey('vacancies.hh_id'), nullable=False)
    error_text = Column(String, nullable=False)
    error_type = Column(String, nullable=True)  # можно хранить тип: 'network', 'auth', 'response_failed' и т.п.
    created_at = Column(DateTime, server_default=func.now())
    reported = Column(Boolean, default=False)  # отправлено ли пользователю
    reported_at = Column(DateTime, nullable=True)  # когда отправлено


class AccountDailyState(Base):
    """Ежедневное состояние аккаунта: лимит, выполнено, паузы"""
    __tablename__ = 'account_daily_state'

    id = Column(Integer, primary_key=True)
    account_id = Column(BigInteger, ForeignKey('accounts.id'), nullable=False)
    date = Column(Date, nullable=False)  # день, к которому относится состояние
    daily_limit = Column(Integer, nullable=False)  # сколько нужно сделать сегодня
    done_count = Column(Integer, default=0)  # сколько уже сделано
    pauses = Column(JSON, nullable=False)  # список пауз: [{"start": "HH:MM", "end": "HH:MM"}, ...]
    status = Column(String, default='pending')  # pending, active, paused, completed
    pause_until = Column(DateTime, nullable=True)  # если в паузе, до какого момента
    completed_at = Column(DateTime, nullable=True)  # когда завершён день (достигнут лимит или конец дня)
    last_updated = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Индексы для быстрого поиска
    __table_args__ = (
        # уникальность: один аккаунт на дату
        UniqueConstraint('account_id', 'date', name='uq_account_daily_state'),
    )


class ParsingLog(Base):
    """Лог сессий парсинга"""
    __tablename__ = 'parsing_log'

    id = Column(Integer, primary_key=True)
    window_index = Column(Integer)  # 0,1,2 (какое окно)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    vacancies_found = Column(Integer, default=0)
    status = Column(String, nullable=False)  # success, failed
    error = Column(String, nullable=True)


class ParsingProgress(Base):
    __tablename__ = 'parsing_progress'
    id = Column(Integer, primary_key=True)
    search_url = Column(String, nullable=False, unique=True)  # или хеш, но для простоты сам URL
    last_page = Column(Integer, default=0)  # номер последней успешно обработанной страницы (0 – ещё не начинали)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class FilteredOutVacancy(Base):
    __tablename__ = 'filtered_out_vacancies'
    hh_id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    url = Column(String, nullable=False)
    filter_reason = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class JuniorVacancy(Base):
    __tablename__ = 'junior_vacancies'

    hh_id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    url = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    score = Column(Integer, default=0)  # <-- добавить
    is_actual = Column(Boolean, default=True)  # <-- добавить
    used_count = Column(Integer, default=0)  # <-- добавить


class SeniorVacancy(Base):
    __tablename__ = 'senior_vacancies'

    hh_id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    url = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    score = Column(Integer, default=0)  # <-- добавить
    is_actual = Column(Boolean, default=True)  # <-- добавить
    used_count = Column(Integer, default=0)  # <-- добавить


class Setting(Base):
    __tablename__ = 'settings'
    key = Column(String, primary_key=True)
    value = Column(String)
