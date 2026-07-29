import os
import json
import asyncio
import logging
from backend.kaspi_wrapper import KaspiAPI
from backend.state_helpers import _get_token, _apply_overrides
from backend.security.store_context import list_active_stores

logger = logging.getLogger(__name__)

PRODUCTS_FILE = "states/products.json"

async def _sync_store_products(token: str, store_id=None) -> int:
    """
    Синхронизирует каталог товаров для одного магазина и перезаписывает
    states/products.json. Возвращает количество синхронизированных товаров.

    store_id=None (легаси) — нескоуп-применение оверрайдов, как было всегда.
    store_id задан (Phase 7) — оверрайды применяются в рамках стора.
    """
    products = await asyncio.to_thread(KaspiAPI.sync_merchant_assortment, token, store_id)

    if not products:
        logger.warning(f"⚠️ [Products Sync Worker] Получен пустой список товаров из Kaspi API (store {store_id}).")
        return 0

    products = _apply_overrides(products, store_id=store_id)
    os.makedirs("states", exist_ok=True)
    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False, indent=2)

    logger.info(f"✅ [Products Sync Worker] Успешно обновлено {len(products)} товаров из Kaspi API (store {store_id}).")
    return len(products)

async def run_products_sync_cycle():
    """
    Один цикл синхронизации каталога (Phase 7). Падает на legacy глобальную
    сессию, только если в БД ещё нет ни одного Store.

    ВАЖНО: states/products.json — единый файл на все сторы (известное
    ограничение, каталог per-store ещё не реализован — см. HANDOFF). Если
    активных сторов больше одного, синхронизируем только первый по порядку
    создания, чтобы второй не затирал первого гонкой перезаписи одного и
    того же файла — предупреждаем в логе вместо тихой порчи данных.
    """
    stores = await list_active_stores()
    if stores:
        if len(stores) > 1:
            logger.warning(
                f"⚠️ [Products Sync Worker] {len(stores)} активных сторов, а states/products.json общий на всех "
                "(per-store каталог ещё не реализован). Синхронизируется только первый стор."
            )
        store = stores[0]
        try:
            await _sync_store_products(store.token, store.store_id)
        except Exception as e:
            logger.error(f"❌ [Products Sync Worker] Ошибка синхронизации для стора {store.store_id}: {e}")
    else:
        token = _get_token()
        if not token:
            logger.warning("⚠️ [Products Sync Worker] Токен авторизации не найден. Пропуск цикла автосинхронизации.")
            return
        try:
            await _sync_store_products(token, None)
        except Exception as e:
            logger.error(f"❌ [Products Sync Worker] Ошибка во время автосинхронизации товаров: {e}")

async def start_products_sync_loop(interval_seconds: int = 1800):
    """
    Фоновый воркер периодической автосинхронизации ассортимента товаров с Kaspi API.
    По умолчанию запускается каждые 30 минут (1800 секунд).
    """
    logger.info(f"🔄 [Products Sync Worker] Фоновый воркер обновления товаров запущен (интервал: {interval_seconds}s).")

    # При запуске первая синхронизация выполняется через 10 секунд
    await asyncio.sleep(10)

    while True:
        try:
            await run_products_sync_cycle()
        except Exception as e:
            logger.error(f"❌ [Products Sync Worker] Ошибка во время автосинхронизации товаров: {e}")

        await asyncio.sleep(interval_seconds)
