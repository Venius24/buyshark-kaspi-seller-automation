"""
Роутер "Сделайте всё за меня" — подключение магазина по SMS владельца.
Логика шагов в кабинете Kaspi — backend/services/kaspi_auto_connect.py.

Поток запросов фронтенда:
  POST /start    {phone}          → SMS владельцу
  POST /verify   {phone, code}    → вход владельца; если магазинов у него
                                    несколько — {status: choose_merchant,
                                    merchants}, иначе сразу {status: started, job_id}
  POST /complete {merchant_id}    → выбранный магазин → {status: started, job_id}
  GET  /jobs/{job_id}             → {status, step, error, store}

Вся работа после SMS (токен, сотрудник, письмо, вход сотрудника) занимает
1–2 минуты — это фоновая задача в лупе приложения, а фронтенд опрашивает её
статус: держать HTTP-запрос открытым столько времени нельзя.

Состояние (владельческие куки между /verify и /complete, задачи) — в памяти
процесса: uvicorn запущен с --workers 1, и SMS-сессии playwright_worker и так
живут в памяти того же процесса.

Выключено, пока в .env не выставлено AUTO_CONNECT_ENABLED=1.
"""

import os
import time
import uuid
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from backend.db.database import AsyncSessionLocal
from backend.db.models import User, ClientMailbox, Store, UserStore
from backend.routers.auth_v1 import get_current_user
from backend.routers.stores_v1 import check_quota_guard, persist_connected_store
from backend.services import kaspi_auto_connect as auto
from backend.services import kaspi_login_code
from backend.services.client_mailbox import provision_mailbox
from backend.services.playwright_worker import worker, Command
from backend.state_helpers import _save_chat_cookies_for_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/stores/auto-connect", tags=["Auto-Connect (владелец)"])

# Сколько живёт вход владельца между /verify и /complete (выбор магазина).
_PENDING_TTL_SECONDS = 600
# Сколько хранится результат задачи после завершения (для опроса фронтендом).
_JOB_TTL_SECONDS = 3600

_pending: dict = {}  # user_id -> {"cookies", "merchants", "created"}
_jobs: dict = {}  # job_id -> {"user_id", "status", "step", "error", "store", "created"}
_tasks: set = set()  # сильные ссылки на asyncio-задачи, иначе их может собрать GC


def _auto_connect_enabled() -> bool:
    return os.getenv("AUTO_CONNECT_ENABLED", "0") == "1"


def _guard_enabled():
    if not _auto_connect_enabled():
        raise HTTPException(
            status_code=503,
            detail="Подключение магазина временно недоступно — напишите нам, подключим вручную.",
        )


class StartAutoConnectRequest(BaseModel):
    phone: str


class VerifyAutoConnectRequest(BaseModel):
    phone: str
    code: str


class CompleteAutoConnectRequest(BaseModel):
    merchant_id: str


def _session_key(user_id: str) -> str:
    # Магазина в БД ещё нет — ключ незавершённой SMS-попытки привязан к пользователю.
    return f"autoconnect:{user_id}"


def _evict_expired():
    now = time.monotonic()
    for user_id in [u for u, p in _pending.items() if now - p["created"] > _PENDING_TTL_SECONDS]:
        _pending.pop(user_id, None)
    for job_id in [
        j for j, job in _jobs.items()
        if job["status"] != "running" and now - job["created"] > _JOB_TTL_SECONDS
    ]:
        _jobs.pop(job_id, None)


def _running_job_for(user_id: str) -> Optional[str]:
    for job_id, job in _jobs.items():
        if job["user_id"] == user_id and job["status"] == "running":
            return job_id
    return None


def _start_job(user_id: str, owner_cookies: dict, merchant_id: str) -> str:
    running = _running_job_for(user_id)
    if running:
        return running
    job_id = uuid.uuid4().hex
    _jobs[job_id] = {
        "user_id": user_id,
        "status": "running",
        "step": "prepare",
        "error": None,
        "store": None,
        "created": time.monotonic(),
    }
    task = asyncio.create_task(_run_job(job_id, user_id, owner_cookies, merchant_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job_id


async def _run_job(job_id: str, user_id: str, owner_cookies: dict, merchant_id: str):
    job = _jobs[job_id]

    def step(name: str):
        job["step"] = name

    try:
        async with AsyncSessionLocal() as db:
            await _check_quota_for_merchant(db, user_id, merchant_id)
            mailbox = await provision_mailbox(db, owner_user_id=user_id, label=f"m{merchant_id}")
            mailbox_id, employee_email = mailbox.id, mailbox.email
            await db.commit()

        since = kaspi_login_code.utcnow()
        cabinet = await asyncio.to_thread(auto.setup_merchant, owner_cookies, merchant_id, employee_email, step)

        step("email")
        temp_password = await asyncio.to_thread(kaspi_login_code.wait_for_temp_password, employee_email, since)
        if not temp_password:
            raise auto.AutoConnectError("Письмо Kaspi с доступом сотрудника не пришло.")

        step("login")
        success, msg, cookies = await asyncio.to_thread(
            worker.send_cmd, Command.LOGIN, {"email": employee_email, "password": temp_password}
        )
        if not success:
            raise auto.AutoConnectError(f"Не удалось войти сотрудником BuyShark: {msg}")

        step("saving")
        async with AsyncSessionLocal() as db:
            user = await db.get(User, user_id)
            name = cabinet["name"]
            if not name:
                # Имя из кабинета не прочиталось — у уже подключённого магазина
                # оставляем прежнее, а не затираем заглушкой.
                name = (await db.execute(select(Store.name).where(Store.merchant_id == merchant_id))).scalars().first()
            store = await persist_connected_store(
                user=user,
                name=name or f"Магазин {merchant_id}",
                api_token=cabinet["api_token"],
                merchant_id=merchant_id,
                cookies_str="; ".join(f"{k}={v}" for k, v in cookies.items()),
                email=employee_email,
                password=temp_password,
            )
        async with AsyncSessionLocal() as db:
            mailbox = (await db.execute(select(ClientMailbox).where(ClientMailbox.id == mailbox_id))).scalar_one()
            mailbox.store_id = store.id
            await db.commit()
        # SMS-сессия владельца — та самая, что нужна чату (email-сессия для него не годится).
        await asyncio.to_thread(_save_chat_cookies_for_store, store.id, owner_cookies)

        job["store"] = store.model_dump()
        job["status"] = "done"
        step("done")
        logger.info("Авто-подключение: магазин %s (%s) подключён для user=%s", store.name, merchant_id, user_id)
    except auto.AutoConnectError as e:
        job["status"] = "error"
        job["error"] = str(e)
        logger.warning("Авто-подключение %s остановлено на шаге %s: %s", merchant_id, job["step"], e)
    except Exception:
        job["status"] = "error"
        job["error"] = "Внутренняя ошибка при подключении магазина — мы уже видим её в логах."
        logger.exception("Авто-подключение %s упало на шаге %s", merchant_id, job["step"])


async def _check_quota_for_merchant(db, user_id: str, merchant_id: str):
    """Лимит магазинов тарифа — только для нового магазина: переподключение
    уже своего (напр. после смены кред) лимит не расходует."""
    already_own = (await db.execute(
        select(UserStore.store_id)
        .join(Store, Store.id == UserStore.store_id)
        .where(UserStore.user_id == user_id, Store.merchant_id == merchant_id)
    )).first()
    if already_own:
        return
    try:
        await check_quota_guard(user_id, db)
    except HTTPException as e:
        raise auto.AutoConnectError(str(e.detail))


@router.post("/start")
async def start_auto_connect(
    req: StartAutoConnectRequest,
    user: User = Depends(get_current_user),
):
    """Шаг 1 — отправить SMS-код на телефон владельца кабинета Kaspi MC."""
    _guard_enabled()
    success, msg, _ = await asyncio.to_thread(auto.start_owner_sms_login, _session_key(user.id), req.phone)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "sms_sent"}


@router.post("/verify")
async def verify_auto_connect(
    req: VerifyAutoConnectRequest,
    user: User = Depends(get_current_user),
):
    """Шаг 2 — код из SMS: вход владельца, список его магазинов."""
    _guard_enabled()
    _evict_expired()
    success, msg, owner_cookies = await asyncio.to_thread(auto.submit_owner_sms_code, _session_key(user.id), req.code)
    if not success or not owner_cookies:
        raise HTTPException(status_code=400, detail=msg or "Не удалось войти по коду из SMS")

    try:
        merchants = await asyncio.to_thread(auto.list_owner_merchants, owner_cookies)
    except auto.AutoConnectError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not merchants:
        raise HTTPException(status_code=400, detail="В кабинете Kaspi не найдено ни одного магазина.")

    if len(merchants) == 1:
        return {"status": "started", "job_id": _start_job(user.id, owner_cookies, merchants[0])}

    _pending[user.id] = {"cookies": owner_cookies, "merchants": merchants, "created": time.monotonic()}
    return {"status": "choose_merchant", "merchants": merchants}


@router.post("/complete")
async def complete_auto_connect(
    req: CompleteAutoConnectRequest,
    user: User = Depends(get_current_user),
):
    """Шаг 3 (если магазинов несколько) — выбранный магазин."""
    _guard_enabled()
    _evict_expired()
    pending = _pending.get(user.id)
    if not pending:
        raise HTTPException(status_code=400, detail="Вход владельца устарел — отправьте SMS заново.")
    if req.merchant_id not in pending["merchants"]:
        raise HTTPException(status_code=400, detail="Этого магазина нет в кабинете владельца.")
    _pending.pop(user.id, None)
    return {"status": "started", "job_id": _start_job(user.id, pending["cookies"], req.merchant_id)}


@router.get("/jobs/{job_id}")
async def auto_connect_job_status(
    job_id: str,
    user: User = Depends(get_current_user),
):
    job = _jobs.get(job_id)
    if not job or job["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="Задача подключения не найдена")
    return {k: job[k] for k in ("status", "step", "error", "store")}
