import logging
from typing import Dict

logger = logging.getLogger(__name__)


def load_cookies_from_netscape(file_path: str) -> Dict[str, str]:
    """
    Загружает cookies из файла в формате Netscape (как экспортирует расширение Get cookies.txt).
    Возвращает словарь {имя: значение}.
    """
    cookies = {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) >= 7:
                    # Формат: domain, flag, path, secure, expiration, name, value
                    name = parts[5]
                    value = parts[6]
                    cookies[name] = value
        logger.info(f"Загружено {len(cookies)} cookies из {file_path}")
    except Exception as e:
        logger.error(f"Ошибка загрузки cookies из {file_path}: {e}")
        raise
    return cookies
