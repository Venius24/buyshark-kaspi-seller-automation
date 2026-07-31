import asyncio
import logging
import requests
from datetime import datetime
from sqlalchemy import select
from backend.db.database import AsyncSessionLocal
from backend.db.models import Store, UserStore, User
from backend.security.crypto import decrypt_data
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
            decrypted_cookies = decrypt_data(store.encrypted_cookies)
            if not decrypted_cookies:
                continue

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Cookie": decrypted_cookies
            }

            try:
                r = await asyncio.to_thread(
                    requests.get, "https://mc.shop.kaspi.kz/merchantcab/api/user/current", headers=headers, timeout=5
                )
                if r.status_code == 200:
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
