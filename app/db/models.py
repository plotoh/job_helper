# app/db/models.py

from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


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


class Account(Base):
    __tablename__ = 'accounts'

    id = Column(Integer, primary_key=True)
    tg_username = Column(String, unique=True, nullable=True)
    hh_username = Column(String, nullable=True)
    hh_password_encrypted = Column(String, nullable=True)
    cookies = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())
    resume_id = Column(String, nullable=False)


class AccountVacancy(Base):
    __tablename__ = 'account_vacancy'

    account_id = Column(Integer, ForeignKey('accounts.id'), primary_key=True)
    hh_id = Column(String, ForeignKey('vacancies.hh_id'), primary_key=True)
    responded = Column(Boolean, default=False)
    response_status = Column(String, nullable=True)
    response_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
