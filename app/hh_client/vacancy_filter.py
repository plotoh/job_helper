import re
from app.hh_client.base_interfaces import IVacancyFilter


class VacancyFilter(IVacancyFilter):
    # === Что МЫ ИЩЕМ (regular) ===
    # Только Python / backend / ML / AI / data engineering.
    INCLUDE_PATTERNS = [
        # Python
        r'python',
        r'питон',
        # Backend
        r'back[\s-]?end',  # backend, back-end, бэкенд
        r'бэкенд',
        # ML / AI / LLM
        r'\bml\b',
        r'machine[ -]?learning',
        r'машинное обучение',
        r'\bai\b',
        r'\bии\b',
        r'artificial[ -]?intelligence',
        r'искусственный интеллект',
        r'\bllm\b',
        r'\bnlp\b',
        r'computer[ -]?vision',
        r'компьютерное зрение',
        r'ml[ -]?engineer',
        r'ml[ -]?инженер',
        r'mlops',
        r'ml[ -]?ops',
        r'mlops[ -]?инженер',
        r'data[ -]?scientist',
        r'дата[ -]?сайентист',
        r'data[ -]?engineer',
        r'data[ -]?инженер',
        r'big[ -]?data',
        r'биг[ -]?дата',
    ]

    # === Junior / intern → junior_vacancies ===
    JUNIOR_PATTERNS = [
        r'стажёр',
        r'стажер',
        r'\bintern\b',
        r'\bjunior\b',
        r'\btrainee\b',
        r'начинающий',
    ]

    # === Senior / lead → senior_vacancies ===
    SENIOR_PATTERNS = [
        r'\bsenior\b',
        r'\blead\b',
        r'team[\s-]?lead',
        r'tech[\s-]?lead',
        r'\bcto\b',
        r'ведущий',
        r'руководитель',
    ]

    # === Глобальные исключения: НЕ сохраняются вообще ===
    EXCLUDE_PATTERNS = [
        # Другие языки
        r'c\+\+',
        r'\bgo\b',
        r'\bphp\b',
        r'frontend',
        r'фронтенд',
        r'\bjava\b',
        r'c#',
        r'full[\s-]?stack',
        r'фулстек',
        r'фул[\s-]?стек',
        r'1с',  # 1С
        r'\b1c\b',

        # QA / тестирование — НЕ нужно
        r'\bqa\b',
        r'\baqa\b',
        r'quality[ -]?assurance',
        r'тестировщик',
        r'тестирование',
        r'\btester\b',
        r'automation[ -]?qa',
        r'qa[ -]?automation',
        r'qa[ -]?engineer',
        r'qa[ -]?инженер',
        r'qa[ -]?автоматизац',

        # DevOps / SRE / инфраструктура — НЕ нужно
        r'\bdevops\b',
        r'девопс',
        r'\bsre\b',
        r'site[ -]?reliability',
        r'системный администратор',
        r'сисадмин',
        r'\bsysadmin\b',

        # Аналитика — НЕ нужно
        r'аналитик',  # ловит: бизнес-аналитик, системный аналитик, дата-аналитик и т.д.
        r'\banalyst\b',
        r'\banalytics\b',  # аналитика как отдельная роль
        r'business[ -]?analyst',
        r'product[ -]?analyst',
        r'data[ -]?analyst',
        r'bi[ -]?analyst',
        r'financial[ -]?analyst',
        r'системный аналитик',
        r'бизнес[ -]?аналитик',
        r'бизнес[ -]?analysis',
        r'product[ -]?analytics',
        r'продуктовый аналитик',

        # Менеджмент / продакты — НЕ нужно
        r'product[ -]?manager',
        r'продакт',
        r'product[ -]?owner',
        r'\bпм\b',
        r'project[ -]?manager',
        r'проектный менеджер',
        r'менеджер продукта',
        r'руководитель продукта',

        # Продюсер / креатив — НЕ нужно
        r'продюсер',
        r'\bproducer\b',
        r'creative[ -]?producer',
        r'креативный продюсер',

        # Безопасность — НЕ нужно
        r'devsecops',
        r'application[ -]?security',
        r'app[ -]?sec',
        r'security[ -]?researcher',
        r'информационная безопасность',
        r'иб[ -]?специалист',
    ]

    def categorize(self, title: str) -> str:
        """
        - 'reject'  – не сохраняется
        - 'junior'  – в JuniorVacancy
        - 'senior'  – в SeniorVacancy (после include-проверки)
        - 'regular' – в основную таблицу (после парсинга)
        """
        title_lower = title.lower()

        # 1. Глобальные исключения — приоритет №1
        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, title_lower):
                return 'reject'

        # 2. Junior
        for pattern in self.JUNIOR_PATTERNS:
            if re.search(pattern, title_lower):
                # junior пропускаем только если он из IT-домена
                if self._matches_include(title_lower):
                    return 'junior'
                return 'reject'

        # 3. Senior
        for pattern in self.SENIOR_PATTERNS:
            if re.search(pattern, title_lower):
                if self._matches_include(title_lower):
                    return 'senior'
                return 'reject'

        # 4. Regular
        if self._matches_include(title_lower):
            return 'regular'

        # 5. Всё остальное — reject
        return 'reject'

    def _matches_include(self, title_lower: str) -> bool:
        for pattern in self.INCLUDE_PATTERNS:
            if re.search(pattern, title_lower):
                return True
        return False

    def is_match(self, title: str) -> bool:
        return self.categorize(title) == 'regular'
