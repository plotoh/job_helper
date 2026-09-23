# app/utils/proxy_utils.py
"""
Нормализация параметров прокси.

Всегда возвращает server с явной схемой (http://, https://, socks4://, socks5://).
Принимает строки вида:
    188.130.220.60:3000
    http://188.130.220.60:3000
    socks5://188.130.220.60:3001
    socks5://user:pass@188.130.220.60:3001
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

VALID_SCHEMES = ("http://", "https://", "socks4://", "socks5://")


def normalize_proxy_server(server: Optional[str]) -> Optional[str]:
    """
    Приводит server к формату со схемой.

    - None      → None    (использовать глобальный из .env)
    - ""        → ""      (явно без прокси)
    - "host:port"            → "http://host:port"
    - "http://host:port"     → без изменений
    - "socks5://host:port"   → без изменений
    """
    if server is None:
        return None
    if server == "":
        return ""

    server = server.strip()
    if not server:
        return ""

    if server.startswith(VALID_SCHEMES):
        return server

    # Схемы нет — по умолчанию http://
    logger.debug(f"Adding http:// scheme to proxy server: {server}")
    return f"http://{server}"


def build_proxy_dict(account) -> Optional[dict]:
    """
    Собирает словарь proxy для Playwright из полей Account.

    Возвращает:
    - None  → create_context сам подставит глобальный прокси из settings
    - {}    → явно без прокси
    - dict  → {'server': 'http://...', 'username': ..., 'password': ...}
    """
    server = normalize_proxy_server(account.proxy_server)

    if server is None:
        return None
    if server == "":
        return {}

    proxy = {"server": server}
    if account.proxy_username:
        proxy["username"] = account.proxy_username
    if account.proxy_password:
        proxy["password"] = account.proxy_password
    return proxy
