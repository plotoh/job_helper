import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


def load_cookies_from_netscape(file_path: str) -> Dict[str, str]:
    """
    Загружает cookies из файла в формате Netscape (только имя и значение).
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
                    name = parts[5]
                    value = parts[6]
                    cookies[name] = value
        logger.info(f"Загружено {len(cookies)} cookies из {file_path}")
    except Exception as e:
        logger.error(f"Ошибка загрузки cookies из {file_path}: {e}")
        raise
    return cookies


def load_cookies_full(file_path: str) -> List[dict]:
    """
    Загружает cookies из файла в формате Netscape и возвращает список словарей
    с полными атрибутами, необходимыми для Playwright (name, value, domain, path, secure, httpOnly, sameSite).
    """
    cookies_list = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) >= 7:
                    domain = parts[0]
                    path = parts[2]
                    secure = parts[3].upper() == 'TRUE'
                    name = parts[5]
                    value = parts[6]
                    cookie = {
                        "name": name,
                        "value": value,
                        "domain": domain,
                        "path": path,
                        "secure": secure,
                        "httpOnly": False,  # из файла не определить, ставим False
                        "sameSite": "Lax",
                    }
                    cookies_list.append(cookie)
        logger.info(f"Загружено {len(cookies_list)} cookies из {file_path}")
    except Exception as e:
        logger.error(f"Ошибка загрузки cookies из {file_path}: {e}")
        raise
    return cookies_list
