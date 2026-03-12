from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from app.bot.config import ADMIN_ID, USERS_IDS


class AccessMiddleware(BaseMiddleware):
    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any]
    ) -> Any:
        user_id = None
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id

        if user_id and user_id not in USERS_IDS and user_id != ADMIN_ID:
            if isinstance(event, Message):
                await event.answer("⛔ У вас нет доступа к этому боту. Но его и не должно быть)")
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Нет доступа", show_alert=True)
            return

        # Добавляем user_id в data для использования в хендлерах
        data['user_id'] = user_id
        data['is_admin'] = (user_id == ADMIN_ID)
        return await handler(event, data)

