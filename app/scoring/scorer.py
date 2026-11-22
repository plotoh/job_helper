# app/scoring/scorer.py
import logging
from datetime import datetime
from typing import Optional, Dict, Any

from app.utils.time_utils import msk_now

logger = logging.getLogger(__name__)


class VacancyScorer:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or self._default_config()

    def _default_config(self) -> Dict[str, Any]:
        return {
            "title_keywords": {
                "python": 30,
                "python разработчик": 40,
                "питон": 30,
                "бэкенд": 20,
                "backend": 20,
                "fullstack": 5,
                "фулстек": 5,
                "ml": 2,
                "машинное обучение": 2,
                "ai": 3,
                "ии": 3,
                "искусственный интеллект": 3,
                "llm": 3,
                "data": 6,
                "данные": 6,
                "engineer": 5,
                "инженер": 5,
                "разработчик": 20,
                "программист": 5,
                "developer": 10,
                "специалист": 4,
                "quality assurance": -10,
                "автоматизация": 5,
                "automation": 5,
                "junior": -5,
                "джуниор": -5,
                "middle": 5,
                "мидл": 5,
                "senior": 8,
                "сеньор": 8,
                "lead": 10,
                "лид": 10,
                "аналитик": -100,
                "analyst": -100,
                "qa": -100,
                "aqa": -100,
                "тестировщик": -100,
                "tester": -100,
                "devops": -100,
                "девопс": -100,
                "sre": -100,
                "product manager": -100,
                "продакт": -100,
                "продюсер": -100,
                "producer": -100,
            },
            "experience_bonus": {
                "min_experience": 2,
                "max_experience": 6,
                "bonus": 10,
            },
            "format_bonus": {
                "remote": 3,
                "удален": 3,
                "удалённо": 3,
                "гибрид": 2,
                "hybrid": 2,
                "офис": -2,
                "office": -2,
            },
            # Freshness — отдельная шкала, НЕ входит в score.
        }

    def compute(self, vacancy: dict) -> int:
        """
        Релевантность вакансии: 0-100.
        Свежесть НЕ учитывается — она отдельно в compute_freshness().
        """
        score = 0.0
        found_keywords = set()

        # 1. Заголовок
        title = vacancy.get('title', '').lower()
        for word, weight in self.config['title_keywords'].items():
            if word in title and word not in found_keywords:
                score += weight
                found_keywords.add(word)

        level_bonus = 0
        level_weights = {
            'junior': -5, 'джуниор': -5,
            'middle': 5, 'мидл': 5,
            'senior': 8, 'сеньор': 8,
            'lead': -5, 'лид': -5,
        }
        for word, bonus in level_weights.items():
            if word in title:
                if bonus > level_bonus:
                    level_bonus = bonus
        score += level_bonus

        # 2. Опыт
        experience = vacancy.get('experience')
        if experience and isinstance(experience, (tuple, list)) and len(experience) == 2:
            exp_min, exp_max = experience
            if exp_min is not None and exp_max is not None:
                if (self.config['experience_bonus']['min_experience']
                        <= exp_min
                        <= self.config['experience_bonus']['max_experience']):
                    score += self.config['experience_bonus']['bonus']

        # 3. Формат работы
        work_format = vacancy.get('work_format', '').lower() if vacancy.get('work_format') else ''
        for fmt, bonus in self.config['format_bonus'].items():
            if fmt in work_format:
                score += bonus
                break

        return int(min(100, max(0, score)))

    def compute_freshness(self, created_at) -> int:
        """
        Отдельная шкала свежести: 0-100.
        Сегодня → 100, вчера → 90, позавчера → 85, 3 дня → 75, 7+ → 0.
        """
        if not created_at:
            return 0

        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            except Exception:
                return 0

        # Убираем tz, если есть — сравниваем naive MSK
        if getattr(created_at, 'tzinfo', None) is not None:
            created_at_naive = created_at.replace(tzinfo=None)
        else:
            created_at_naive = created_at

        now = msk_now().replace(tzinfo=None)
        days_ago = (now - created_at_naive).days

        if days_ago <= 0:
            return 100
        if days_ago == 1:
            return 90
        if days_ago == 2:
            return 85
        if days_ago == 3:
            return 75
        if days_ago >= 7:
            return 0

        # Линейная интерполяция между 3 днями (75) и 7 днями (0)
        return max(0, int(round(75 * (7 - days_ago) / 4)))
