from datetime import date
from typing import Optional, List, Dict, Any
from sqlalchemy import select, update, and_, DateTime, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import AccountDailyState

# Sentinel для различения "не передано" и "передано None"
_UNSET = object()


class AccountStateRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_for_today(self, account_id: int, today: date) -> Optional[AccountDailyState]:
        """Получить состояние аккаунта на сегодня"""
        stmt = select(AccountDailyState).where(
            and_(
                AccountDailyState.account_id == account_id,
                AccountDailyState.date == today
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_or_update(self, account_id: int, today: date, data: Dict[str, Any]) -> AccountDailyState:
        """Создать новую запись или обновить существующую (если уже есть)"""
        # Проверяем, есть ли уже
        existing = await self.get_for_today(account_id, today)
        if existing:
            # Обновляем поля, кроме id, account_id, date
            for key, value in data.items():
                if hasattr(existing, key) and key not in ('id', 'account_id', 'date'):
                    setattr(existing, key, value)
            await self.session.commit()
            await self.session.refresh(existing)
            return existing
        else:
            new_state = AccountDailyState(
                account_id=account_id,
                date=today,
                **data
            )
            self.session.add(new_state)
            await self.session.commit()
            await self.session.refresh(new_state)
            return new_state

    async def increment_done(self, account_id: int, today: date) -> int:
        """Увеличить счётчик выполненных откликов на 1 и вернуть новое значение"""
        stmt = (
            update(AccountDailyState)
            .where(
                and_(
                    AccountDailyState.account_id == account_id,
                    AccountDailyState.date == today
                )
            )
            .values(done_count=AccountDailyState.done_count + 1)
            .returning(AccountDailyState.done_count)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.scalar_one()

    async def update_status(
            self,
            account_id: int,
            today: date,
            status: str,
            pause_until: Any = _UNSET,
    ):
        """
        Обновить статус и, опционально, время окончания паузы.

        Если pause_until не передан — поле не трогается.
        Если pause_until=None — поле явно очищается в БД.
        Если pause_until=<datetime> — поле устанавливается.
        """
        values = {'status': status}
        if pause_until is not _UNSET:
            values['pause_until'] = pause_until  # может быть None — это очистит поле
        stmt = (
            update(AccountDailyState)
            .where(
                and_(
                    AccountDailyState.account_id == account_id,
                    AccountDailyState.date == today
                )
            )
            .values(**values)
        )
        await self.session.execute(stmt)
        await self.session.commit()

    async def mark_completed(self, account_id: int, today: date):
        """Отметить день как завершённый (достигнут лимит или конец рабочего дня)"""
        stmt = (
            update(AccountDailyState)
            .where(
                and_(
                    AccountDailyState.account_id == account_id,
                    AccountDailyState.date == today
                )
            )
            .values(status='completed', completed_at=func.now())
        )
        await self.session.execute(stmt)
        await self.session.commit()
