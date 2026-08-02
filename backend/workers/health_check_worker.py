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
    """
    logger.info("🏥 Запуск фоновой проверки живых куков Kaspi (Health Check)...")

    async with AsyncSessionLocal() as db:
        stmt = select(Store).where(Store.status == "ACTIVE")
        res = await db.execute(stmt)
        active_stores = res.scalars().all()

        for store in active_stores:
            mc_cookies = _load_mc_cookies(store_id=store.id)
            if not mc_cookies:
                continue

            # Раньше здесь был GET .../merchantcab/api/user/current — этот
            # эндпоинт у Kaspi отдаёт 404 ВСЕГДА, независимо от валидности
            # куков (проверено вживую: BFF-запросы теми же куками работают,
            # этот "health-check" — нет). Из-за этого этот воркер раз в час
            # помечал COOKIE_EXPIRED и слал ложную Telegram-тревогу для
            # ЛЮБОГО магазина, даже с полностью рабочими куками. Заменено на
            # тот же реальный browser-based VERIFY_SESSION, что уже
            # проверенно работает в /api/mc-status?verify=true.
            try:
                success, is_valid, _fresh = await asyncio.to_thread(worker.send_cmd, Command.VERIFY_SESSION, mc_cookies)
                if success and is_valid:
                    store.last_sync_at = datetime.utcnow()
                else:
                    logger.warning(f"🚨 Сессия магазина '{store.name}' (Merchant ID: {store.merchant_id}) истекла!")
                    store.status = "COOKIE_EXPIRED"

                    await notify_cookie_expired(f"Сессия магазина {store.name} ({store.merchant_id}) истекла")
            except Exception as e:
                logger.error(f"Ошибка проверки магазина {store.name}: {e}")

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
