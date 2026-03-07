# app/services/crud.py
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Vacancy
import json


async def save_vacancy_if_not_exists(db: AsyncSession, vacancy_data: dict):
    """
    Сохраняет вакансию, только если её ещё нет в БД.
    Использует INSERT ... ON CONFLICT DO NOTHING.
    Возвращает True, если вставка произошла, иначе False.
    """
    stmt = insert(Vacancy).values(
        hh_id=vacancy_data['id'],
        title=vacancy_data['title'],
        url=vacancy_data['url'],
        salary_text=vacancy_data.get('salary'),
        experience_min=vacancy_data.get('experience', (None, None))[0],
        experience_max=vacancy_data.get('experience', (None, None))[1],
        work_format=vacancy_data.get('work_format'),
        skills=json.dumps(vacancy_data.get('skills', []))
    ).on_conflict_do_nothing(index_elements=['hh_id'])
    result = await db.execute(stmt)
    await db.commit()
    return result.rowcount > 0  # >0 если вставка произошла
