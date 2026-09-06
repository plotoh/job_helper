import logging
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

logger = logging.getLogger(__name__)


def admin_keyboard() -> ReplyKeyboardMarkup:
    logger.debug("🎹 Создание админ-клавиатуры")
    buttons = [[KeyboardButton(text="📊 Отчёт")], [KeyboardButton(text="👥 Пользователи")]]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def user_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text="📈 Моя статистика")],
        [KeyboardButton(text="❌ Мои ошибки")],
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)