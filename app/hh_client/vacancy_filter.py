# app/hh_client/vacancy_filter.py
import re

from app.hh_client.base_interfaces import IVacancyFilter


class VacancyFilter(IVacancyFilter):
    INCLUDE_PATTERNS = [
        r'python', r'питон',
        r'back[\s-]?end', r'бэкенд',
        r'\bml\b', r'machine[ -]?learning', r'машинное обучение',
        r'\bai\b', r'\bии\b', r'artificial[ -]?intelligence', r'искусственный интеллект',
        r'\bllm\b', r'\bnlp\b', r'computer[ -]?vision', r'компьютерное зрение',
        r'ml[ -]?engineer', r'ml[ -]?инженер',
        r'mlops', r'ml[ -]?ops', r'mlops[ -]?инженер',
        r'data[ -]?scientist', r'дата[ -]?сайентист',
        r'data[ -]?engineer', r'data[ -]?инженер',
        r'big[ -]?data', r'биг[ -]?дата',
    ]

    JUNIOR_PATTERNS = [
        r'стажёр', r'стажер', r'\bintern\b', r'\bjunior\b', r'\btrainee\b', r'начинающий',
    ]

    SENIOR_PATTERNS = [
        r'\bsenior\b', r'\blead\b', r'team[\s-]?lead', r'tech[\s-]?lead',
        r'\bcto\b', r'ведущий', r'руководитель',
    ]

    EXCLUDE_PATTERNS = [
        # Другие языки
        r'c\+\+', r'\bgo\b', r'golang', r'\bphp\b', r'frontend', r'фронтенд', r'\bjava\b',
        r'c#', r'full[\s-]?stack', r'фулстек', r'фул[\s-]?стек', r'1с', r'\b1c\b',
        r'\bc\b', r'\bmcu\b',
        # QA / тестирование
        r'\bqa\b', r'\baqa\b', r'\bsdet\b', r'quality[ -]?assurance',
        r'тестировщик', r'тестирован\w*', r'\btester\b',
        r'automation[ -]?qa', r'qa[ -]?automation', r'qa[ -]?engineer', r'qa[ -]?инженер',
        r'qa[ -]?автоматизац', r'qa[ -]?lead', r'qa[ -]?manager', r'руководитель\s+qa',
        r'автоматизац\w*\s+тестирован\w*',
        # DevOps / SRE
        r'\bdevops\b', r'девопс', r'\bsre\b', r'site[ -]?reliability',
        r'системный администратор', r'сисадмин', r'\bsysadmin\b',
        # Аналитика
        r'аналитик', r'\banalyst\b', r'\banalytics\b',
        r'business[ -]?analyst', r'product[ -]?analyst', r'data[ -]?analyst',
        r'bi[ -]?analyst', r'financial[ -]?analyst', r'системный аналитик',
        r'бизнес[ -]?аналитик', r'бизнес[ -]?analysis',
        r'product[ -]?analytics', r'продуктовый аналитик',
        r'\bbi\b',
        # Менеджмент
        r'product[ -]?manager', r'продакт', r'product[ -]?owner',
        r'\bпм\b', r'project[ -]?manager', r'проектный менеджер',
        r'менеджер продукта', r'руководитель продукта',
        r'полного[\s-]?цикла', r'full[\s-]?cycle',
        # Продюсеры
        r'продюсер', r'\bproducer\b', r'creative[ -]?producer', r'креативный продюсер',
        # Безопасность
        r'devsecops', r'application[ -]?security', r'app[ -]?sec',
        r'security[ -]?researcher', r'информационная безопасность', r'иб[ -]?специалист',
    ]

    def categorize(self, title: str) -> str:
        title_lower = title.lower()

        for pattern in self.EXCLUDE_PATTERNS:
            if re.search(pattern, title_lower):
                return 'reject'

        for pattern in self.JUNIOR_PATTERNS:
            if re.search(pattern, title_lower):
                return 'junior' if self._matches_include(title_lower) else 'reject'

        for pattern in self.SENIOR_PATTERNS:
            if re.search(pattern, title_lower):
                return 'senior' if self._matches_include(title_lower) else 'reject'

        if self._matches_include(title_lower):
            return 'regular'
        return 'reject'

    def _matches_include(self, title_lower: str) -> bool:
        return any(re.search(p, title_lower) for p in self.INCLUDE_PATTERNS)

    def is_match(self, title: str) -> bool:
        return self.categorize(title) == 'regular'
