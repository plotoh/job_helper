import re
from app.hh_client.base_interfaces import IVacancyFilter


class VacancyFilter(IVacancyFilter):
    # Основные роли, которые нас интересуют (regular)
    INCLUDE_PATTERNS = [
        # Python и backend
        r'python',
        r'back[\s-]?end',          # backend, back-end, бэкенд
        # QA / тестирование (с Python)
        r'qa.*automation',
        r'automation.*qa',
        r'qa[ -]?инженер',
        r'qa.*engineer',
        r'qa.*автоматизац',
        r'automation.*engineer',
        # DevOps / SRE
        r'devops',
        r'sre',
        r'site[ -]?reliability[ -]?engineer',
        # Data
        r'data[ -]?analyst',
        r'data[ -]?аналитик',
        r'product[ -]?analyst',
        r'product[ -]?аналитик',
        r'bi[ -]?аналитик',
        r'bi[ -]?analyst',
        r'big[ -]?data',
        r'data[ -]?engineer',
        r'data[ -]?инженер',
        r'data[ -]?scientist',
        # ML / AI
        r'ml[ -]?engineer',
        r'machine[ -]?learning[ -]?engineer',
        r'ml[ -]?инженер',
        r'nlp[ -]?engineer',
        r'nlp[ -]?инженер',
        r'ai[ -]?engineer',
        r'llm',
        r'ai',
        r'ml',
        r'llm[ -]?engineer',
        r'llm/rag/ai',
        r'ai[ -]?инженер',
        r'computer[ -]?vision[ -]?engineer',
        r'cv[ -]?engineer',
        r'mlops',
        r'mlops[ -]?инженер',
    ]

    # Junior / intern (попадают в junior_vacancies)
    JUNIOR_PATTERNS = [
        r'стажёр',
        r'стажер',
        r'intern',
        r'junior',
        r'trainee',
        r'начинающий',
    ]

    # Senior / lead (попадают в senior_vacancies)
    SENIOR_PATTERNS = [
        r'senior',
        r'lead',
        r'team[\s-]?lead',
        r'tech[\s-]?lead',
        r'cto',
        r'ведущий',
        r'руководитель',
    ]

    # Глобальные исключения (вакансии вообще не сохраняются)
    EXCLUDE_PATTERNS = [
        r'c\+\+',
        r'\bgo\b',
        r'\bphp\b',
        r'frontend',
        r'\bjava\b',
        r'c#',
        r'full[\s-]?stack',          # fullstack, full-stack
        r'devsecops',
        r'application[\s-]?security',
        r'app[\s-]?sec',
        r'security[\s-]?researcher',
    ]

    def categorize(self, title: str) -> str:
        """
        Возвращает категорию вакансии:
        - 'reject'  – не сохраняется (FilteredOutVacancy)
        - 'junior'  – сохраняется в JuniorVacancy
        - 'senior'  – сохраняется в SeniorVacancy (после проверки include)
        - 'regular' – сохраняется в основную таблицу (после детального парсинга)
        """
        title_lower = title.lower()

        # 1. Глобальные исключения
        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, title_lower):
                return 'reject'

        # 2. Junior (наивысший приоритет после исключений)
        for pattern in self.JUNIOR_PATTERNS:
            if re.search(pattern, title_lower):
                return 'junior'

        # 3. Senior
        for pattern in self.SENIOR_PATTERNS:
            if re.search(pattern, title_lower):
                # Senior должен соответствовать INCLUDE_PATTERNS, иначе reject
                if self._matches_include(title_lower):
                    return 'senior'
                else:
                    return 'reject'

        # 4. Проверка на включение в regular
        if self._matches_include(title_lower):
            return 'regular'

        # 5. Всё остальное – reject
        return 'reject'

    def _matches_include(self, title_lower: str) -> bool:
        for pattern in self.INCLUDE_PATTERNS:
            if re.search(pattern, title_lower):
                return True
        return False

    # Для обратной совместимости
    def is_match(self, title: str) -> bool:
        return self.categorize(title) == 'regular'