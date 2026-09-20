import logging
from datetime import datetime
from typing import Optional, Dict, Any

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
            # "skill_keywords": {
            #     "fastapi": 8,
            #     "django": 8,
            #     "django rest framework": 7,
            #     "drf": 7,
            #     "flask": 5,
            #     "aiohttp": 6,
            #     "asyncio": 7,
            #     "postgresql": 8,
            #     "postgres": 7,
            #     "sql": 5,
            #     "mongodb": 5,
            #     "elasticsearch": 6,
            #     "redis": 7,
            #     "rabbitmq": 7,
            #     "kafka": 6,
            #     "celery": 7,
            #     "sqlalchemy": 7,
            #     "alembic": 6,
            #     "docker": 6,
            #     "docker-compose": 5,
            #     "gitlab ci": 5,
            #     "ci/cd": 5,
            #     "pytest": 5,
            #     "poetry": 4,
            #     "nginx": 3,
            #     "linux": 3,
            #     "pandas": 6,
            #     "numpy": 5,
            #     "etl": 6,
            #     "rest api": 6,
            #     "rest": 5,
            #     "api": 5,
            #     "microservices": 6,
            #     "микросервисы": 6,
            #     "solid": 4,
            #     "ооп": 4,
            #     "паттерны": 4,
            # },
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
            "freshness_bonus": {
                "max_days": 5,
                "points_per_day": 1,
                "max_bonus": 5,
            },
        }

    def compute(self, vacancy: dict) -> int:
        score = 0.0
        found_keywords = set()  # для избежания двойного учёта

        # 1. Заголовок
        title = vacancy.get('title', '').lower()
        for word, weight in self.config['title_keywords'].items():
            if word in title and word not in found_keywords:
                score += weight
                found_keywords.add(word)

        level_bonus = 0
        level_weights = {
            'junior': -5,
            'джуниор': -5,
            'middle': 5,
            'мидл': 5,
            'senior': 8,
            'сеньор': 8,
            'lead': -5, # считаю что не готов
            'лид': -5,
        }
        for word, bonus in level_weights.items():
            if word in title:
                if bonus > level_bonus:
                    level_bonus = bonus
        score += level_bonus
        # # 2. Навыки
        # skills = [s.lower() for s in vacancy.get('skills', [])]
        # if skills:
        #     skills_text = ' '.join(skills)
        #     for word, weight in self.config['skill_keywords'].items():
        #         if word in skills_text and word not in found_keywords:
        #             score += weight
        #             found_keywords.add(word)

        # 3. Описание (с половинным весом)
        # description = vacancy.get('description', '').lower() if vacancy.get('description') else ''
        # if description:
        #     for word, weight in self.config['skill_keywords'].items():
        #         if word in description and word not in found_keywords:
        #             score += weight * 0.5
        #             found_keywords.add(word)
        #     for word, weight in self.config['title_keywords'].items():
        #         if word in description and word not in found_keywords:
        #             score += weight * 0.5
        #             found_keywords.add(word)

        # 4. Опыт
        experience = vacancy.get('experience')
        if experience and isinstance(experience, (tuple, list)) and len(experience) == 2:
            exp_min, exp_max = experience
            if exp_min is not None and exp_max is not None:
                if self.config['experience_bonus']['min_experience'] <= exp_min <= self.config['experience_bonus'][
                    'max_experience']:
                    score += self.config['experience_bonus']['bonus']

        # 5. Формат работы
        work_format = vacancy.get('work_format', '').lower() if vacancy.get('work_format') else ''
        for fmt, bonus in self.config['format_bonus'].items():
            if fmt in work_format:
                score += bonus
                break

        # 6. Бонус за свежесть
        freshness_bonus = self._compute_freshness_bonus(vacancy)
        score += freshness_bonus

        return int(min(100, max(0, score)))

    def _compute_freshness_bonus(self, vacancy: dict) -> float:
        created_at = vacancy.get('created_at')
        if not created_at:
            return 0
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            except Exception:
                return 0
        if hasattr(created_at, 'date'):
            now = datetime.now()
            days_ago = (now - created_at).days
            bonus = self.config['freshness_bonus']['max_bonus'] - days_ago
            return max(0, bonus)
        return 0
