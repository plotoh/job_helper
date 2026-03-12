import logging
from aiogram import Router, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from sqlalchemy import select
from app.bot.reporter import generate_report, get_user_stats
from app.bot.keyboards import admin_keyboard, user_keyboard
from app.bot.config import ADMIN_ID, USERS_IDS
from app.db import AsyncSessionLocal, Account

logger = logging.getLogger(__name__)
router = Router()


def is_allowed(user_id: int) -> bool:
    result = user_id in USERS_IDS or user_id == ADMIN_ID
    logger.debug(f"🔐 Проверка доступа для {user_id}: {result}")
    return result


@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    logger.info(f"📩 /start от пользователя {user_id}")

    if not is_allowed(user_id):
        logger.warning(f"⛔ Отказано в доступе для {user_id}")
        await message.answer("⛔ У вас нет доступа к этому боту.")
        return

    if user_id == ADMIN_ID:
        logger.info(f"👑 Админ {user_id} получил меню")
        await message.answer("👋 Привет, админ! Тыкай кнопку", reply_markup=admin_keyboard())
    else:
        logger.info(f"👤 Пользователь {user_id} получил меню")
        await message.answer(
            "👋 Привет! Я бот для упрощения рутины трудоустройства.\n"
            "Используй кнопку ниже для просмотра статистики.",
            reply_markup=user_keyboard()
        )


@router.message(Command("report"))
@router.message(F.text == "📊 Отчёт")
async def cmd_report(message: Message):
    user_id = message.from_user.id
    logger.info(f"📩 /report от пользователя {user_id}")

    if not is_allowed(user_id):
        await message.answer("⛔ У вас нет доступа.")
        return

    if user_id != ADMIN_ID:
        logger.warning(f"⛔ Пользователь {user_id} попытался получить админ-отчёт")
        await message.answer("⛔ Эта команда только для администратора.")
        return

    await message.answer("⏳ Формирую общий отчёт...")
    try:
        report = await generate_report()
        await message.answer(report, parse_mode="Markdown")
        logger.info("✅ Отчёт сформирован и отправлен")
    except Exception as e:
        logger.exception(f"❌ Ошибка при формировании отчёта: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("mystats"))
@router.message(F.text == "📈 Моя статистика")
async def cmd_mystats(message: Message):
    user_id = message.from_user.id
    logger.info(f"📩 /mystats от пользователя {user_id}")

    if not is_allowed(user_id):
        await message.answer("⛔ У вас нет доступа.")
        return

    try:
        stats = await get_user_stats(user_id)
        await message.answer(stats, parse_mode="Markdown")
        logger.info("✅ Статистика пользователя отправлена")
    except Exception as e:
        logger.exception(f"❌ Ошибка при получении статистики: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("users"))
@router.message(F.text == "👥 Пользователи")
async def cmd_users(message: Message):
    user_id = message.from_user.id
    logger.info(f"📩 /users от пользователя {user_id}")

    if not is_allowed(user_id):
        await message.answer("⛔ У вас нет доступа.")
        return

    if user_id != ADMIN_ID:
        await message.answer("⛔ Эта команда только для администратора.")
        return

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Account))
            accounts = result.scalars().all()

        if not accounts:
            await message.answer("Нет зарегистрированных пользователей.")
            return

        lines = ["Список пользователей:"]
        for acc in accounts:
            lines.append(f"ID: {acc.id} (@{acc.tg_username}), резюме: {acc.resume_id}")

        await message.answer("\n".join(lines))
        logger.info("✅ Список пользователей отправлен")
    except Exception as e:
        logger.exception(f"❌ Ошибка при получении списка пользователей: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@router.message(Command("help"))
async def cmd_help(message: Message):
    user_id = message.from_user.id
    logger.info(f"📩 /help от пользователя {user_id}")

    if not is_allowed(user_id):
        await message.answer("⛔ У вас нет доступа.")
        return

    if user_id == ADMIN_ID:
        await message.answer(
            "Команды администратора:\n"
            "/report – общий отчёт\n"
            "/users – список пользователей\n"
            "/help – помощь",
            reply_markup=admin_keyboard()
        )
    else:
        await message.answer(
            "Команды пользователя:\n"
            "/mystats – моя статистика\n"
            "/help – помощь",
            reply_markup=user_keyboard()
        )


@router.message()
async def handle_unknown(message: Message):
    user_id = message.from_user.id
    if not is_allowed(user_id):
        return

    logger.debug(f"💬 Неизвестное сообщение от {user_id}: {message.text}")

    if user_id == ADMIN_ID:
        await message.answer("Не надо ничего писать, используй кнопки.", reply_markup=admin_keyboard())
    else:
        await message.answer(
            "Зачем ты сюда пишешь? У тебя есть кнопка, больше тебе ничего не нужно.",
            reply_markup=user_keyboard()
        )