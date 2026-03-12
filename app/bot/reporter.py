import logging
from datetime import datetime, timedelta
from sqlalchemy import select, func, and_
from app.db.database import AsyncSessionLocal
from app.db.models import ParsingLog, AccountDailyState, ResponseError, Account
from app.utils.time_utils import msk_now

logger = logging.getLogger(__name__)


async def get_parsing_stats() -> dict:
    logger.debug("📊 Запрос статистики парсинга...")
    now = msk_now()
    now_naive = now.replace(tzinfo=None)
    today_start = datetime.combine(now.date(), datetime.min.time())
    hour_ago_naive = now_naive - timedelta(hours=1)

    async with AsyncSessionLocal() as session:
        hour_result = await session.execute(
            select(func.sum(ParsingLog.vacancies_found))
            .where(and_(ParsingLog.finished_at >= hour_ago_naive, ParsingLog.status == 'success'))
        )
        hour_count = hour_result.scalar() or 0

        day_result = await session.execute(
            select(func.sum(ParsingLog.vacancies_found))
            .where(and_(ParsingLog.finished_at >= today_start, ParsingLog.status == 'success'))
        )
        day_count = day_result.scalar() or 0

    logger.debug(f"📊 Парсинг: {hour_count} за час, {day_count} за день")
    return {"hour": hour_count, "day": day_count}


async def get_accounts_stats() -> list:
    logger.debug("📊 Запрос статистики аккаунтов...")
    now = msk_now()
    now_naive = now.replace(tzinfo=None)
    today = now.date()
    hour_ago_naive = now_naive - timedelta(hours=1)

    async with AsyncSessionLocal() as session:
        accounts = (await session.execute(select(Account))).scalars().all()
        stats = []

        for acc in accounts:
            state = await session.execute(
                select(AccountDailyState)
                .where(and_(AccountDailyState.account_id == acc.id, AccountDailyState.date == today))
            )
            state = state.scalar_one_or_none()

            errors = await session.execute(
                select(func.count(ResponseError.id))
                .where(and_(ResponseError.account_id == acc.id, ResponseError.created_at >= hour_ago_naive))
            )
            error_count = errors.scalar() or 0

            if state:
                stats.append({
                    "id": acc.id,
                    "username": acc.tg_username,
                    "done": state.done_count,
                    "limit": state.daily_limit,
                    "status": state.status,
                    "errors": error_count,
                    "pause_until": state.pause_until
                })
            else:
                stats.append({
                    "id": acc.id,
                    "username": acc.tg_username,
                    "done": 0,
                    "limit": "?",
                    "status": "no data",
                    "errors": error_count,
                    "pause_until": None
                })

    logger.debug(f"📊 Найдено аккаунтов: {len(stats)}")
    return stats


async def generate_report() -> str:
    logger.info("📝 Формирование общего отчёта...")
    parsing = await get_parsing_stats()
    accounts = await get_accounts_stats()
    now_naive = msk_now().replace(tzinfo=None)

    lines = [f"📊 **Отчёт за час**", f"🔍 **Парсинг:** {parsing['hour']} за час | {parsing['day']} за день"]

    for acc in accounts:
        status_emoji = {"active": "🟢", "paused": "⏸️", "completed": "✅", "pending": "⏳", "no data": "⚪"}.get(
            acc["status"], "⚪")
        pause_info = ""
        if acc["pause_until"] and acc["pause_until"] > now_naive:
            mins = int((acc["pause_until"] - now_naive).total_seconds() / 60)
            pause_info = f" (пауза {mins} мин)"
        lines.append(
            f"{status_emoji} **{acc['username']}:** {acc['done']} / {acc['limit']} (ошибок: {acc['errors']}){pause_info}")

    lines.append("🕐 Обновление каждый час.")
    report = "\n".join(lines)
    logger.info("✅ Отчёт сформирован")
    return report


async def get_user_stats(telegram_id: int) -> str:
    logger.info(f"📝 Формирование статистики для пользователя {telegram_id}...")
    now = msk_now()
    now_naive = now.replace(tzinfo=None)
    today = now.date()
    hour_ago_naive = now_naive - timedelta(hours=1)

    async with AsyncSessionLocal() as session:
        account = await session.get(Account, telegram_id)
        if not account:
            logger.warning(f"❌ Аккаунт {telegram_id} не найден")
            return "❌ Вы не зарегистрированы в системе."

        state = await session.execute(
            select(AccountDailyState)
            .where(and_(AccountDailyState.account_id == telegram_id, AccountDailyState.date == today))
        )
        state = state.scalar_one_or_none()

        errors = await session.execute(
            select(func.count(ResponseError.id))
            .where(and_(ResponseError.account_id == telegram_id, ResponseError.created_at >= hour_ago_naive))
        )
        error_count = errors.scalar() or 0

        if state:
            status_emoji = {"active": "🟢", "paused": "⏸️", "completed": "✅", "pending": "⏳"}.get(state.status, "⚪")
            pause_info = ""
            if state.pause_until and state.pause_until > now_naive:
                mins = int((state.pause_until - now_naive).total_seconds() / 60)
                pause_info = f" (пауза {mins} мин)"

            logger.info(f"✅ Статистика для {telegram_id} сформирована")
            return f"{status_emoji} **Ваша статистика:**\nСделано: {state.done_count} / {state.daily_limit}\nОшибок за час: {error_count}{pause_info}"
        else:
            logger.info(f"✅ Статистика для {telegram_id} сформирована (нет данных)")
            return f"📊 **Ваша статистика:**\nСделано: 0 / ? (нет данных за сегодня)\nОшибок за час: {error_count}"