import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.utils.time_utils import msk_now
from app.bot.handlers import router
from app.bot.reporter import generate_report
from app.bot.config import ADMIN_ID, BOT_TOKEN, USERS_IDS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def hourly_report(bot_instance: Bot):
    logger.info("🕐 Задача часовых отчётов запущена")
    max_retries = 5
    base_delay = 10

    try:
        while True:
            now = msk_now()
            next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            sleep_seconds = (next_hour - now).total_seconds()
            logger.info(f"⏳ Следующий отчёт через {sleep_seconds:.0f} сек")

            # Разбиваем длинный сон на интервалы с проверкой отмены
            while sleep_seconds > 0:
                chunk = min(sleep_seconds, 60)  # спим по 60 сек максимум
                await asyncio.sleep(chunk)
                sleep_seconds -= chunk

            for attempt in range(max_retries):
                try:
                    report = await generate_report()
                    await bot_instance.send_message(ADMIN_ID, report)
                    logger.info("✅ Часовой отчёт отправлен")
                    break
                except Exception as e:
                    logger.warning(f"⚠️ Попытка {attempt + 1}/{max_retries} не удалась: {type(e).__name__}: {e}")
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.info(f"🔄 Повтор через {delay} сек...")
                        await asyncio.sleep(delay)
                    else:
                        logger.error("❌ Все попытки отправки отчёта исчерпаны")
    except asyncio.CancelledError:
        logger.info("🛑 Задача часовых отчётов отменена")
        raise


async def main():
    logger.info("🚀 Бот запускается...")
    logger.info(f"📋 Admin ID: {ADMIN_ID}")
    logger.info(f"📋 Users IDs: {USERS_IDS}")

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    logger.info(f"✅ Бот инициализирован: {bot.id}")

    dp = Dispatcher()
    dp.include_router(router)
    logger.info("✅ Роутер подключён")

    # Сохраняем задачу для возможности отмены
    report_task = asyncio.create_task(hourly_report(bot_instance=bot))
    logger.info("✅ Задача отчётов создана")

    logger.info("🔄 Запуск polling...")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.exception(f"❌ Ошибка polling: {e}")
    finally:
        report_task.cancel()
        try:
            await report_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()
        logger.info("🛑 Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
