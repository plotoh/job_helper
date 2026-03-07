# app/services/filters.py
import re


def is_vacancy_match(title: str) -> bool:
    """
    Проверяет, подходит ли вакансия по названию.
    Условия:
    - Должен присутствовать "python" ИЛИ ("backend" или вариации)
    - Не должно быть исключений: c++, go, php, frontend, java, стажер, c#, qa, devops
    """
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
