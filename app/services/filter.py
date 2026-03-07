import re
from app.services.interfaces import IVacancyFilter


class VacancyFilter(IVacancyFilter):
    def is_match(self, title: str) -> bool:
        title_lower = title.lower()
        include_python = 'python' in title_lower
        include_backend = bool(re.search(r'back[\s-]?end', title_lower))
        if not (include_python or include_backend):
            return False
        exclude_patterns = [
            r'c\+\+', r'\bgo\b', r'\bphp\b', r'frontend', r'\bjava\b',
            r'стажер', r'c#', r'\bqa\b', r'devops'
        ]
        for pattern in exclude_patterns:
            if re.search(pattern, title_lower):
                return False
        return True
