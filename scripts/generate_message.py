#!/usr/bin/env python3
"""
Скрипт для локальной генерации сопроводительного письма по шаблону.
Использует функцию generate_message из app.hh_client.message_generator.

Пример запуска:
    python scripts/generate_message.py --vacancy "Python разработчик" --tg "@peyego1one"
    {Хорошего дня|Удачного дня|Всего наилучшего|С уважением|С наилучшими пожеланиями}{!|.}
"""

import os
import sys
import argparse
from pathlib import Path

# Добавляем корень проекта в sys.path, чтобы импортировать модули app
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.hh_client.message_generator import generate_message


def load_template(template_path: Path) -> str:
    """Загружает шаблон из файла."""
    if not template_path.exists():
        raise FileNotFoundError(f"Файл шаблона {template_path} не найден")
    return template_path.read_text(encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description="Генерация письма по шаблону")
    parser.add_argument("--vacancy", default="Python разработчик", help="Название вакансии")
    parser.add_argument("--tg", default="@peyego1one", help="Telegram username")
    parser.add_argument("--template", default=PROJECT_ROOT / "message_template.txt",
                        help="Путь к файлу шаблона (по умолчанию message_template.txt в корне проекта)")
    args = parser.parse_args()

    template_path = Path(args.template)
    try:
        template = load_template(template_path)
    except FileNotFoundError as e:
        print(f"Ошибка: {e}")
        print("Укажите правильный путь к шаблону через --template")
        sys.exit(1)

    context = {
        "vacancy_name": args.vacancy,
        "tg_username": args.tg,
    }

    result = generate_message(template, context)
    print("\n" + "="*50)
    print("СГЕНЕРИРОВАННОЕ ПИСЬМО:")
    print("="*50)
    print(result)
    print("="*50)


if __name__ == "__main__":
    main()