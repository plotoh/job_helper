# app/web/main.py
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import asc, desc, func, select

from app.config import settings
from app.db import (
    Account,
    AccountDailyState,
    AccountVacancy,
    AsyncSessionLocal,
    ResponseError,
    Vacancy,
)
from app.db.repositories.account_repo import AccountRepository
from app.db.repositories.account_state_repo import AccountStateRepository
from app.utils.settings_utils import get_setting, set_setting
from app.utils.time_utils import msk_now
from app.utils.proxy_utils import normalize_proxy_server

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="HH Parser Dashboard")
templates = Jinja2Templates(directory="app/web/templates")


# ---------- Pydantic ----------
class PauseRequest(BaseModel):
    duration_minutes: Optional[int] = None


class AccountCreate(BaseModel):
    id: int
    tg_username: str
    resume_id: str
    is_enabled: bool = True
    proxy_server: Optional[str] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    work_start_hour: Optional[int] = None
    work_start_minute: Optional[int] = None
    work_end_hour: Optional[int] = None
    work_end_minute: Optional[int] = None
    pause_count: Optional[int] = None
    pause_min_minutes: Optional[int] = None
    pause_max_minutes: Optional[int] = None
    min_gap_minutes: Optional[int] = None
    daily_limit_min: Optional[int] = None
    daily_limit_max: Optional[int] = None


class AccountUpdate(AccountCreate):
    id: int


# ---------- Pages ----------
@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/vacancies")
async def vacancies_page(request: Request):
    return templates.TemplateResponse("vacancies.html", {"request": request})


@app.get("/logs")
async def view_logs(request: Request):
    log_dir = Path("logs/responses")
    logs = []
    if log_dir.exists():
        for file in sorted(log_dir.glob("responses_*.txt"), reverse=True)[:5]:
            with open(file, "r", encoding="utf-8") as f:
                logs.extend(f.readlines()[-50:])
    return templates.TemplateResponse("logs.html", {"request": request, "logs": logs})


@app.get("/worker-logs")
async def view_worker_logs(request: Request):
    log_file = Path("logs/worker.log")
    logs = []
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            logs = f.readlines()[-200:]
    return templates.TemplateResponse("worker_logs.html", {"request": request, "logs": logs})


@app.get("/accounts/new")
async def new_account_page(request: Request):
    return templates.TemplateResponse("account_form.html", {
        "request": request,
        "account": None,
        "is_new": True,
        "cookies_exists": False,
    })


@app.get("/accounts/{account_id}/edit")
async def edit_account_page(request: Request, account_id: int):
    async with AsyncSessionLocal() as session:
        account = await AccountRepository(session).get(account_id)
        if not account:
            raise HTTPException(404, "Account not found")
        cookies_path = Path(settings.COOKIES_DIR) / f"{account.tg_username}.txt"
        return templates.TemplateResponse("account_form.html", {
            "request": request,
            "account": account,
            "is_new": False,
            "cookies_exists": cookies_path.exists(),
        })


@app.get("/accounts/{account_id}/logs")
async def account_logs(request: Request, account_id: int):
    log_file = Path(f"logs/accounts/{account_id}.log")
    logs = []
    if log_file.exists():
        with open(log_file, "r", encoding="utf-8") as f:
            logs = f.readlines()[-300:]
    return templates.TemplateResponse("worker_logs.html", {
        "request": request,
        "logs": logs,
    })


@app.get("/accounts/{account_id}")
async def account_detail(request: Request, account_id: int):
    async with AsyncSessionLocal() as session:
        account = await AccountRepository(session).get(account_id)
        if not account:
            raise HTTPException(404, "Account not found")

        today = msk_now().date()
        state = await AccountStateRepository(session).get_for_today(account_id, today)

        responses = (await session.execute(
            select(AccountVacancy)
            .where(AccountVacancy.account_id == account_id)
            .order_by(desc(AccountVacancy.response_date))
            .limit(20)
        )).scalars().all()

        errors = (await session.execute(
            select(ResponseError)
            .where(ResponseError.account_id == account_id)
            .order_by(desc(ResponseError.created_at))
            .limit(20)
        )).scalars().all()

        return templates.TemplateResponse("account.html", {
            "request": request,
            "account": account,
            "state": state,
            "responses": responses,
            "errors": errors,
        })


# ---------- API: аккаунты CRUD ----------
@app.get("/api/accounts")
async def list_accounts():
    async with AsyncSessionLocal() as session:
        accounts = await AccountRepository(session).list_all()
        return [
            {
                "id": a.id,
                "tg_username": a.tg_username,
                "resume_id": a.resume_id,
                "is_enabled": a.is_enabled,
                "proxy_server": a.proxy_server,
                "proxy_username": a.proxy_username,
                # proxy_password намеренно не отдаём
                "work_start_hour": a.work_start_hour,
                "work_start_minute": a.work_start_minute,
                "work_end_hour": a.work_end_hour,
                "work_end_minute": a.work_end_minute,
                "pause_count": a.pause_count,
                "pause_min_minutes": a.pause_min_minutes,
                "pause_max_minutes": a.pause_max_minutes,
                "min_gap_minutes": a.min_gap_minutes,
                "daily_limit_min": a.daily_limit_min,
                "daily_limit_max": a.daily_limit_max,
                "cookies_exists": (
                        Path(settings.COOKIES_DIR) / f"{a.tg_username}.txt"
                ).exists(),
            }
            for a in accounts
        ]

@app.post("/api/accounts")
async def create_account(data: AccountCreate):
    async with AsyncSessionLocal() as session:
        repo = AccountRepository(session)
        if await repo.get(data.id):
            raise HTTPException(400, "Account with this id already exists")

        payload = data.model_dump()
        payload["proxy_server"] = normalize_proxy_server(payload.get("proxy_server"))
        await repo.create(payload)
        return {"status": "ok", "id": data.id}


@app.patch("/api/accounts/{account_id}")
async def update_account(account_id: int, data: AccountUpdate):
    async with AsyncSessionLocal() as session:
        repo = AccountRepository(session)
        payload = data.model_dump(exclude={"id"})

        # Нормализуем прокси при сохранении
        if "proxy_server" in payload:
            payload["proxy_server"] = normalize_proxy_server(payload.get("proxy_server"))

        # Если proxy_password пустой — не перетираем
        if not payload.get("proxy_password"):
            payload.pop("proxy_password", None)

        acc = await repo.update(account_id, payload)
        if not acc:
            raise HTTPException(404, "Account not found")
        return {"status": "ok"}


@app.delete("/api/accounts/{account_id}")
async def delete_account(account_id: int):
    async with AsyncSessionLocal() as session:
        ok = await AccountRepository(session).delete(account_id)
        if not ok:
            raise HTTPException(404, "Account not found")
        return {"status": "ok"}


# ---------- API: настройки аккаунта (для старого UI) ----------
class AccountSettingsUpdate(BaseModel):
    resume_id: str
    daily_limit: int


@app.post("/api/accounts/{account_id}/settings")
async def update_account_settings(account_id: int, data: AccountSettingsUpdate):
    async with AsyncSessionLocal() as session:
        account = await AccountRepository(session).get(account_id)
        if not account:
            raise HTTPException(404, "Account not found")
        account.resume_id = data.resume_id

        today = msk_now().date()
        state_repo = AccountStateRepository(session)
        state = await state_repo.get_for_today(account_id, today)
        if state:
            state.daily_limit = data.daily_limit
        else:
            from app.scheduler.daily_planner import generate_pauses
            pauses = generate_pauses()
            session.add(AccountDailyState(
                account_id=account_id, date=today,
                daily_limit=data.daily_limit, pauses=pauses,
                done_count=0, status='pending',
            ))
        await session.commit()
        return {"status": "ok"}


# ---------- API: pause / resume / manual ----------
@app.post("/api/accounts/{account_id}/pause")
async def api_pause_account(account_id: int, request: PauseRequest):
    today = msk_now().date()
    async with AsyncSessionLocal() as session:
        repo = AccountStateRepository(session)
        state = await repo.get_for_today(account_id, today)
        if not state:
            raise HTTPException(404, "Account state not found")

        # Считаем aware-время (для возврата в API), но в БД пишем naive
        pause_until_aware = (
            msk_now() + timedelta(days=365)
            if request.duration_minutes is None
            else msk_now() + timedelta(minutes=request.duration_minutes)
        )
        state.status = 'paused'
        state.pause_until = pause_until_aware.replace(tzinfo=None)  # ← ключ
        state.manual_override = False
        await session.commit()
        return {"status": "ok", "pause_until": pause_until_aware.isoformat()}


@app.post("/api/accounts/{account_id}/resume")
async def api_resume_account(account_id: int):
    today = msk_now().date()
    async with AsyncSessionLocal() as session:
        repo = AccountStateRepository(session)
        state = await repo.get_for_today(account_id, today)
        if not state:
            raise HTTPException(404, "Account state not found")
        state.status = 'active'
        state.pause_until = None
        state.manual_override = False  # обычный режим расписания
        await session.commit()
        return {"status": "ok", "mode": "schedule"}


@app.post("/api/accounts/{account_id}/manual")
async def api_manual_account(account_id: int, enabled: bool = Query(...)):
    today = msk_now().date()
    async with AsyncSessionLocal() as session:
        repo = AccountStateRepository(session)
        state = await repo.get_for_today(account_id, today)
        if not state:
            raise HTTPException(404, "Account state not found")
        state.manual_override = enabled
        if enabled:
            state.status = 'active'
            state.pause_until = None
        await session.commit()
        return {"status": "ok", "manual_override": enabled}


# ---------- API: статистика ----------
@app.get("/api/stats")
async def api_stats():
    async with AsyncSessionLocal() as session:
        total_vacancies = (await session.execute(
            select(func.count(Vacancy.hh_id))
        )).scalar() or 0
        actual_vacancies = (await session.execute(
            select(func.count(Vacancy.hh_id)).where(Vacancy.is_actual == True)
        )).scalar() or 0
        total_accounts = (await session.execute(
            select(func.count(Account.id))
        )).scalar() or 0

        today = msk_now().date()
        start_of_day = datetime.combine(today, datetime.min.time())

        responses_today_total = (await session.execute(
            select(func.count(AccountVacancy.hh_id))
            .where(AccountVacancy.response_date >= start_of_day)
        )).scalar() or 0

        errors_today_total = (await session.execute(
            select(func.count(ResponseError.id))
            .where(ResponseError.created_at >= start_of_day)
        )).scalar() or 0

        accounts = await AccountRepository(session).list_all()
        accounts_data = []
        for acc in accounts:
            state = await AccountStateRepository(session).get_for_today(acc.id, today)

            resp_acc = (await session.execute(
                select(func.count(AccountVacancy.hh_id))
                .where(
                    AccountVacancy.account_id == acc.id,
                    AccountVacancy.response_date >= start_of_day,
                    AccountVacancy.response_status.in_(['success', 'wo_soprovod']),
                )
            )).scalar() or 0

            tests_acc = (await session.execute(
                select(func.count(AccountVacancy.hh_id))
                .where(
                    AccountVacancy.account_id == acc.id,
                    AccountVacancy.response_date >= start_of_day,
                    AccountVacancy.response_status == 'test_required',
                )
            )).scalar() or 0

            errs_acc = (await session.execute(
                select(func.count(ResponseError.id))
                .where(
                    ResponseError.account_id == acc.id,
                    ResponseError.created_at >= start_of_day,
                )
            )).scalar() or 0

            accounts_data.append({
                "id": acc.id,
                "username": acc.tg_username,
                "resume_id": acc.resume_id,
                "is_enabled": acc.is_enabled,
                "done": state.done_count if state else 0,
                "limit": state.daily_limit if state else "?",
                "status": state.status if state else "no data",
                "pause_until": state.pause_until.isoformat() if state and state.pause_until else None,
                "manual_override": bool(state.manual_override) if state else False,
                "responses_today": resp_acc,
                "tests_today": tests_acc,
                "errors_today": errs_acc,
            })

        return {
            "vacancies_total": total_vacancies,
            "vacancies_actual": actual_vacancies,
            "accounts_total": total_accounts,
            "responses_today": responses_today_total,
            "errors_today": errors_today_total,
            "accounts": accounts_data,
        }


# ---------- API: вакансии ----------
@app.get("/api/vacancies")
async def api_vacancies(
        page: int = Query(1, ge=1),
        per_page: int = Query(50, ge=1, le=200),
        sort_by: str = Query("created_at"),
        order: str = Query("desc"),
        status: str = Query("all"),
):
    async with AsyncSessionLocal() as session:
        stmt = select(Vacancy)
        if status == "actual":
            stmt = stmt.where(Vacancy.is_actual == True)
        elif status == "not_actual":
            stmt = stmt.where(Vacancy.is_actual == False)

        order_func = asc if order == "asc" else desc
        if sort_by == "title":
            stmt = stmt.order_by(order_func(Vacancy.title))
        elif sort_by == "score":
            stmt = stmt.order_by(order_func(Vacancy.score))
        elif sort_by == "used_count":
            stmt = stmt.order_by(order_func(Vacancy.used_count))
        elif sort_by == "is_actual":
            stmt = stmt.order_by(order_func(Vacancy.is_actual))
        else:
            stmt = stmt.order_by(order_func(Vacancy.created_at))

        total = (await session.execute(
            select(func.count()).select_from(stmt.subquery())
        )).scalar() or 0
        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        vacancies = (await session.execute(stmt)).scalars().all()

        return {
            "items": [
                {
                    "hh_id": v.hh_id,
                    "title": v.title,
                    "url": v.url,
                    "score": v.score,
                    "used_count": v.used_count,
                    "is_actual": v.is_actual,
                    "created_at": v.created_at.isoformat() if v.created_at else None,
                    "salary_text": v.salary_text,
                }
                for v in vacancies
            ],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page,
        }


# ---------- API: прочее ----------
@app.post("/api/vacancies/recalculate-scores")
async def recalculate_scores(force: bool = Query(False)):
    try:
        from app.utils.update_vacancies import update_vacancies
        await update_vacancies(force=force)
        return {"status": "ok",
                "message": "Скор пересчитан" + (" принудительно" if force else " (только новые)")}
    except Exception as e:
        raise HTTPException(500, str(e))


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
