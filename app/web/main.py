import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select, func, desc, asc

from app.db import AsyncSessionLocal, Account, Vacancy, AccountVacancy, ResponseError, AccountDailyState
from app.db.repositories.account_state_repo import AccountStateRepository
from app.utils.time_utils import msk_now
from app.utils.settings_utils import get_setting, set_setting

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="HH Parser Dashboard")

# Шаблоны
templates = Jinja2Templates(directory="app/web/templates")


# Статика (опционально)
# app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

# ---------- Модели для API ----------
class PauseRequest(BaseModel):
    duration_minutes: Optional[int] = None  # None = бессрочно


class AccountSettingsUpdate(BaseModel):
    resume_id: str
    daily_limit: int


@app.get("/api/accounts/{account_id}/settings")
async def get_account_settings(account_id: int):
    async with AsyncSessionLocal() as session:
        account = await session.get(Account, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        today = msk_now().date()
        state_repo = AccountStateRepository(session)
        state = await state_repo.get_for_today(account_id, today)
        return {
            "resume_id": account.resume_id,
            "daily_limit": state.daily_limit if state else None,
        }


@app.post("/api/accounts/{account_id}/settings")
async def update_account_settings(account_id: int, data: AccountSettingsUpdate):
    async with AsyncSessionLocal() as session:
        account = await session.get(Account, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        # Обновляем resume_id
        account.resume_id = data.resume_id
        # Обновляем daily_limit на сегодня
        today = msk_now().date()
        state_repo = AccountStateRepository(session)
        state = await state_repo.get_for_today(account_id, today)
        if state:
            state.daily_limit = data.daily_limit
        else:
            # Если нет состояния, создаём с этим лимитом
            from app.scheduler.daily_planner import generate_pauses
            pauses = generate_pauses()
            new_state = AccountDailyState(
                account_id=account_id,
                date=today,
                daily_limit=data.daily_limit,
                pauses=pauses,
                done_count=0,
                status='pending'
            )
            session.add(new_state)
        await session.commit()
        return {"status": "ok"}


@app.post("/api/vacancies/recalculate-scores")
async def recalculate_scores(force: bool = Query(False)):
    try:
        from app.utils.update_vacancies import update_vacancies
        await update_vacancies(force=force)
        return {"status": "ok", "message": "Скор пересчитан" + (" принудительно" if force else " (только новые)")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/worker-logs")
async def view_worker_logs(request: Request):
    """Страница просмотра логов воркера."""
    log_file = Path("logs/worker.log")
    logs = []
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            logs = lines[-200:]  # последние 200 строк
    return templates.TemplateResponse("worker_logs.html", {"request": request, "logs": logs})


@app.get("/accounts/{account_id}/edit")
async def edit_account(request: Request, account_id: int):
    async with AsyncSessionLocal() as session:
        account = await session.get(Account, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        today = msk_now().date()
        state_repo = AccountStateRepository(session)
        state = await state_repo.get_for_today(account_id, today)
        return templates.TemplateResponse("account_edit.html", {
            "request": request,
            "account": account,
            "state": state,
        })


# ---------- Вспомогательные функции ----------
async def get_account_state(account_id: int, today: datetime.date):
    async with AsyncSessionLocal() as session:
        repo = AccountStateRepository(session)
        return await repo.get_for_today(account_id, today)


# ---------- Страницы (HTML) ----------
@app.get("/")
async def index(request: Request):
    """Главная страница с дашбордом."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/vacancies")
async def vacancies_page(request: Request):
    """Страница со списком вакансий."""
    return templates.TemplateResponse("vacancies.html", {"request": request})


@app.get("/api/vacancies")
async def api_vacancies(
        page: int = Query(1, ge=1),
        per_page: int = Query(50, ge=1, le=200),
        sort_by: str = Query("created_at"),
        order: str = Query("desc"),
        status: str = Query("all"),  # all, actual, not_actual
):
    """Возвращает список вакансий с пагинацией и сортировкой."""
    async with AsyncSessionLocal() as session:
        # Базовый запрос
        stmt = select(Vacancy)

        # Фильтр по статусу
        if status == "actual":
            stmt = stmt.where(Vacancy.is_actual == True)
        elif status == "not_actual":
            stmt = stmt.where(Vacancy.is_actual == False)
        # all – без фильтра

        # Сортировка
        if sort_by == "title":
            order_func = asc if order == "asc" else desc
            stmt = stmt.order_by(order_func(Vacancy.title))
        elif sort_by == "score":
            order_func = asc if order == "asc" else desc
            stmt = stmt.order_by(order_func(Vacancy.score))
        elif sort_by == "used_count":
            order_func = asc if order == "asc" else desc
            stmt = stmt.order_by(order_func(Vacancy.used_count))
        elif sort_by == "is_actual":
            order_func = asc if order == "asc" else desc
            stmt = stmt.order_by(order_func(Vacancy.is_actual))
        else:  # created_at
            order_func = asc if order == "asc" else desc
            stmt = stmt.order_by(order_func(Vacancy.created_at))

        # Пагинация
        total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0
        offset = (page - 1) * per_page
        stmt = stmt.offset(offset).limit(per_page)
        result = await session.execute(stmt)
        vacancies = result.scalars().all()

        # Подсчёт количества откликов для каждой вакансии (можно через подзапрос, но для простоты загрузим отдельно)
        # В текущей модели у Vacancy есть поле used_count, которое обновляется при откликах – используем его.
        # Также можно добавить информацию, откликался ли текущий аккаунт (если нужно).

        data = []
        for vac in vacancies:
            data.append({
                "hh_id": vac.hh_id,
                "title": vac.title,
                "url": vac.url,
                "score": vac.score,
                "used_count": vac.used_count,
                "is_actual": vac.is_actual,
                "created_at": vac.created_at.isoformat() if vac.created_at else None,
                "salary_text": vac.salary_text,
            })

        return {
            "items": data,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }


@app.get("/accounts/{account_id}")
async def account_detail(request: Request, account_id: int):
    """Детальная страница аккаунта."""
    async with AsyncSessionLocal() as session:
        account = await session.get(Account, account_id)
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")

        # Состояние на сегодня
        today = msk_now().date()
        state_repo = AccountStateRepository(session)
        state = await state_repo.get_for_today(account_id, today)

        # История откликов (последние 20)
        stmt = (
            select(AccountVacancy)
            .where(AccountVacancy.account_id == account_id)
            .order_by(desc(AccountVacancy.response_date))
            .limit(20)
        )
        responses = (await session.execute(stmt)).scalars().all()

        # Ошибки (последние 20)
        stmt_errors = (
            select(ResponseError)
            .where(ResponseError.account_id == account_id)
            .order_by(desc(ResponseError.created_at))
            .limit(20)
        )
        errors = (await session.execute(stmt_errors)).scalars().all()

        return templates.TemplateResponse("account.html", {
            "request": request,
            "account": account,
            "state": state,
            "responses": responses,
            "errors": errors,
        })


@app.get("/logs")
async def view_logs(request: Request):
    """Страница просмотра логов откликов."""
    log_dir = Path("logs/responses")
    logs = []
    if log_dir.exists():
        for file in sorted(log_dir.glob("responses_*.txt"), reverse=True)[:5]:  # последние 5 файлов
            with open(file, "r", encoding="utf-8") as f:
                lines = f.readlines()
                # берём последние 50 строк из каждого файла
                logs.extend(lines[-50:])
    return templates.TemplateResponse("logs.html", {"request": request, "logs": logs})


# ---------- API (JSON) ----------
@app.get("/api/stats")
async def api_stats():
    async with AsyncSessionLocal() as session:
        total_vacancies = (await session.execute(select(func.count(Vacancy.hh_id)))).scalar() or 0
        actual_vacancies = (await session.execute(
            select(func.count(Vacancy.hh_id)).where(Vacancy.is_actual == True))).scalar() or 0
        total_accounts = (await session.execute(select(func.count(Account.id)))).scalar() or 0

        today = msk_now().date()
        start_of_day = datetime.combine(today, datetime.min.time())

        responses_today = (await session.execute(
            select(func.count())  # <-- исправлено
            .where(AccountVacancy.response_date >= start_of_day)
        )).scalar() or 0

        errors_today = (await session.execute(
            select(func.count(ResponseError.id))
            .where(ResponseError.created_at >= start_of_day)
        )).scalar() or 0
        # Аккаунты с их состоянием
        accounts = (await session.execute(select(Account))).scalars().all()
        accounts_data = []
        for acc in accounts:
            state = await AccountStateRepository(session).get_for_today(acc.id, today)
            accounts_data.append({
                "id": acc.id,
                "username": acc.tg_username,
                "resume_id": acc.resume_id,
                "done": state.done_count if state else 0,
                "limit": state.daily_limit if state else "?",
                "status": state.status if state else "no data",
                "pause_until": state.pause_until.isoformat() if state and state.pause_until else None,
            })

        return {
            "vacancies_total": total_vacancies,
            "vacancies_actual": actual_vacancies,
            "accounts_total": total_accounts,
            "responses_today": responses_today,
            "errors_today": errors_today,
            "accounts": accounts_data,
        }


@app.get("/api/accounts/{account_id}/history")
async def api_account_history(account_id: int, limit: int = 20):
    """История откликов и ошибок для графиков."""
    async with AsyncSessionLocal() as session:
        # Отклики за последние 7 дней (группировка по дням)
        # Упрощённо: вернём список последних откликов с датами
        stmt_responses = (
            select(AccountVacancy.response_date, AccountVacancy.response_status)
            .where(AccountVacancy.account_id == account_id)
            .order_by(desc(AccountVacancy.response_date))
            .limit(limit)
        )
        responses = (await session.execute(stmt_responses)).all()

        stmt_errors = (
            select(ResponseError.created_at, ResponseError.error_type)
            .where(ResponseError.account_id == account_id)
            .order_by(desc(ResponseError.created_at))
            .limit(limit)
        )
        errors = (await session.execute(stmt_errors)).all()

        return {
            "responses": [{"date": r[0].isoformat(), "status": r[1]} for r in responses],
            "errors": [{"date": e[0].isoformat(), "type": e[1]} for e in errors],
        }


@app.post("/api/accounts/{account_id}/resume")
async def api_resume_account(account_id: int):
    today = msk_now().date()
    async with AsyncSessionLocal() as session:
        repo = AccountStateRepository(session)
        state = await repo.get_for_today(account_id, today)
        if not state:
            raise HTTPException(status_code=404, detail="Account state not found")
        state.status = 'active'
        state.pause_until = None
        state.manual_override = True  # <-- ключевое: игнорируем расписание и паузы
        await session.commit()
        return {"status": "ok"}


@app.post("/api/accounts/{account_id}/pause")
async def api_pause_account(account_id: int, request: PauseRequest):
    today = msk_now().date()
    async with AsyncSessionLocal() as session:
        repo = AccountStateRepository(session)
        state = await repo.get_for_today(account_id, today)
        if not state:
            raise HTTPException(status_code=404, detail="Account state not found")
        if request.duration_minutes is None:
            pause_until = msk_now() + timedelta(days=365)
        else:
            pause_until = msk_now() + timedelta(minutes=request.duration_minutes)
        state.status = 'paused'
        state.pause_until = pause_until
        state.manual_override = False  # <-- возвращаем обычное расписание
        await session.commit()
        return {"status": "ok", "pause_until": pause_until.isoformat()}


# @app.post("/api/accounts/{account_id}/pause")
# async def api_pause_account(account_id: int, request: PauseRequest):
#     """Поставить аккаунт на паузу."""
#     today = msk_now().date()
#     async with AsyncSessionLocal() as session:
#         repo = AccountStateRepository(session)
#         state = await repo.get_for_today(account_id, today)
#         if not state:
#             raise HTTPException(status_code=404, detail="Account state not found")
#
#         if request.duration_minutes is None:
#             pause_until = msk_now() + timedelta(days=365)  # бессрочно
#         else:
#             pause_until = msk_now() + timedelta(minutes=request.duration_minutes)
#
#         await repo.update_status(account_id, today, 'paused', pause_until=pause_until)
#         return {"status": "ok", "pause_until": pause_until.isoformat()}
#
#
# @app.post("/api/accounts/{account_id}/resume")
# async def api_resume_account(account_id: int):
#     """Снять паузу с аккаунта."""
#     today = msk_now().date()
#     async with AsyncSessionLocal() as session:
#         repo = AccountStateRepository(session)
#         await repo.update_status(account_id, today, 'active', pause_until=None)
#         return {"status": "ok"}


@app.post("/api/settings/responses/enable")
async def enable_responses():
    await set_setting('responses_enabled', 'true')
    return {"status": "ok"}


@app.post("/api/settings/responses/disable")
async def disable_responses():
    await set_setting('responses_enabled', 'false')
    return {"status": "ok"}


@app.get("/api/settings/responses/status")
async def responses_status():
    val = await get_setting('responses_enabled', 'true')
    return {"enabled": val == 'true'}


@app.post("/api/parsing/start")
async def force_parsing():
    await set_setting('force_parsing', 'true')
    return {"status": "ok", "message": "Парсинг будет запущен в течение 10 секунд"}


@app.post("/api/settings/include-junior")
async def set_include_junior(enabled: bool = Query(...)):
    await set_setting('include_junior', 'true' if enabled else 'false')
    return {"status": "ok"}


@app.post("/api/settings/include-senior")
async def set_include_senior(enabled: bool = Query(...)):
    await set_setting('include_senior', 'true' if enabled else 'false')
    return {"status": "ok"}


@app.get("/api/settings/include-junior")
async def get_include_junior_status():
    val = await get_setting('include_junior', 'true')
    return {"enabled": val == 'true'}


@app.get("/api/settings/include-senior")
async def get_include_senior_status():
    val = await get_setting('include_senior', 'true')
    return {"enabled": val == 'true'}


# ---------- Запуск ----------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
