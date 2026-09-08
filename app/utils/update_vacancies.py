import asyncio
import logging
from datetime import datetime
from sqlalchemy import select

from app.config import settings
from app.db import AsyncSessionLocal
from app.db.models import Vacancy
from app.scoring.scorer import VacancyScorer

logger = logging.getLogger(__name__)


async def update_vacancies(force: bool = False):
    """
    Обновляет is_actual и score для всех вакансий.
    - force=True  → пересчитывает score для всех вакансий.
    - force=False → пересчитывает только для вакансий с score == 0.
    """
    logger.info("🔄 Запуск обновления вакансий (force=%s)...", force)
    scorer = VacancyScorer()
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Vacancy))
        vacancies = result.scalars().all()
        total = len(vacancies)
        updated_actual = 0
        updated_score = 0

        for idx, vac in enumerate(vacancies, 1):
            # 1. Актуальность (всегда проверяем)
            is_actual = True
            if vac.created_at and (datetime.now() - vac.created_at).days >= settings.VACANCY_TTL_DAYS:
                is_actual = False
            if vac.used_count >= settings.MAX_USES_PER_VACANCY:
                is_actual = False

            if vac.is_actual != is_actual:
                vac.is_actual = is_actual
                updated_actual += 1

            # 2. Скор – пересчитываем только при force=True или если текущий скор равен 0
            if force or vac.score == 0:
                vacancy_dict = {
                    'title': vac.title,
                    'skills': vac.skills if vac.skills else [],
                    'description': vac.description if vac.description else '',
                    'experience': (vac.experience_min, vac.experience_max)
                    if (vac.experience_min is not None and vac.experience_max is not None)
                    else None,
                    'work_format': vac.work_format if vac.work_format else '',
                    'created_at': vac.created_at,
                }
                new_score = scorer.compute(vacancy_dict)
                if new_score != vac.score:
                    vac.score = new_score
                    updated_score += 1
                # если значение не изменилось, не увеличиваем счётчик обновлённых

            if idx % 100 == 0:
                await session.commit()
                logger.info(f"⏳ Обработано {idx}/{total}")

        await session.commit()
        logger.info(f"✅ Обновлено: {updated_actual} записей актуальности, {updated_score} записей скора.")


# Для запуска отдельно (с force=True)
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(update_vacancies(force=True))