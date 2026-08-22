import asyncio
import logging
from datetime import datetime
from sqlalchemy import select
from backend.db.database import AsyncSessionLocal
from backend.db.models import Store, UserStore, User
from backend.state_helpers import _load_mc_cookies
from backend.services.playwright_worker import worker, Command
from backend.chatbot.notifications import notify_cookie_expired

logger = logging.getLogger(__name__)


async def check_all_stores_health():
    """
    Фоновый Health-Check воркер:
    Проверяет статус живых сессий куков Kaspi для всех подключенных магазинов.
    Если куки слетели — меняет статус магазина на COOKIE_EXPIRED и шлет Telegram-уведомление.

    Пул Postgres: сессия НЕ держится открытой во время VERIFY_SESSION (это
    запуск браузера, десятки секунд на магазин). Раньше одна сессия
    обнимала весь цикл проверок всех сторов — коннект пула был занят на
    десятки минут за проход. Теперь: короткая сессия на чтение списка,
    проверки без сессии, короткая сессия на запись статусов.
    """
    logger.info("🏥 Запуск фоновой проверки живых куков Kaspi (Health Check)...")

    async with AsyncSessionLocal() as db:
        stmt = select(Store.id, Store.name, Store.merchant_id).where(Store.status == "ACTIVE")
        res = await db.execute(stmt)
        active_stores = res.all()

    # (store_id, ok, checked_at) — результаты собираются вне сессии.
    results = []

    for store_id, store_name, merchant_id in active_stores:
        mc_cookies = _load_mc_cookies(store_id=store_id)
        if not mc_cookies:
            continue

        # Раньше здесь был GET .../merchantcab/api/user/current — этот
        # эндпоинт у Kaspi отдаёт 404 ВСЕГДА, независимо от валидности куков
        # (проверено вживую: BFF-запросы теми же куками работают, этот
        # "health-check" — нет). Из-за этого этот воркер раз в час помечал
        # COOKIE_EXPIRED и слал ложную Telegram-тревогу для ЛЮБОГО магазина,
        # даже с полностью рабочими куками. Заменено на тот же реальный
        # browser-based VERIFY_SESSION, что уже проверенно работает в
        # /api/mc-status?verify=true.
        try:
            success, is_valid, _fresh = await asyncio.to_thread(worker.send_cmd, Command.VERIFY_SESSION, mc_cookies)
            if not (success and is_valid):
                logger.warning(f"🚨 Сессия магазина '{store_name}' (Merchant ID: {merchant_id}) истекла!")
                await notify_cookie_expired(f"Сессия магазина {store_name} ({merchant_id}) истекла")
            results.append((store_id, bool(success and is_valid), datetime.utcnow()))
        except Exception as e:
            logger.error(f"Ошибка проверки магазина {store_name}: {e}")

    async with AsyncSessionLocal() as db:
        for store_id, ok, checked_at in results:
            store = await db.get(Store, store_id)
            if not store:
                continue
            if ok:
                store.last_sync_at = checked_at
            else:
                store.status = "COOKIE_EXPIRED"
        await db.commit()
    logger.info("🏥 Проверка куков завершена.")


async def start_health_check_loop(interval_seconds: int = 3600):
    """Бесконечный цикл проверки каждые N секунд"""
    while True:
        try:
            await check_all_stores_health()
        except Exception as e:
            logger.error(f"Ошибка воркера Health-Check: {e}")
        await asyncio.sleep(interval_seconds)
