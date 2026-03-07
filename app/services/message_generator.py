import random
import re
from typing import Dict


def generate_message(template: str, context: Dict[str, str]) -> str:
    """
    Генерирует письмо из шаблона с вариантами {option1|option2|...}
    и подстановкой переменных {var_name} из context.
    """

    def replacer(match):
        inner = match.group(1)
        if '|' in inner:
            # Это группа вариантов
            options = [opt.strip() for opt in inner.split('|')]
            return random.choice(options)
        else:
            # Это простая переменная
            return context.get(inner, f'{{{inner}}}')  # если нет в контексте, оставляем как есть

    # Заменяем все вхождения { ... }
    return re.sub(r'\{([^{}]*)}', replacer, template)
