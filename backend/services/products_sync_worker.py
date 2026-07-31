import asyncio
import logging
from backend.kaspi_wrapper import KaspiAPI
from backend.state_helpers import _get_token, _apply_overrides, _save_products
from backend.security.store_context import list_active_stores

logger = logging.getLogger(__name__)

async def _sync_store_products(token: str, store_id=None) -> int:
    """
    Синхронизирует каталог товаров для одного магазина в его собственный
    кэш (per-store, Слайс 1 — StoreProductDB, см. state_helpers._save_products).
    Возвращает количество синхронизированных товаров.

    store_id=None (легаси) — нескоуп-применение оверрайдов/сохранение в общий
    файл, как было всегда (только dev-бутстрап без Store в БД).
    """
    products = await asyncio.to_thread(KaspiAPI.sync_merchant_assortment, token, store_id)

    if not products:
        logger.warning(f"⚠️ [Products Sync Worker] Получен пустой список товаров из Kaspi API (store {store_id}).")
        return 0

    products = _apply_overrides(products, store_id=store_id)
    await asyncio.to_thread(_save_products, products, store_id)

    logger.info(f"✅ [Products Sync Worker] Успешно обновлено {len(products)} товаров из Kaspi API (store {store_id}).")
    return len(products)

async def run_products_sync_cycle():
    """
    Один цикл синхронизации каталога (Phase 7). Падает на legacy глобальную
    сессию, только если в БД ещё нет ни одного Store.

    Каталог теперь per-store (Слайс 1) — синхронизируем КАЖДЫЙ активный
    магазин отдельно, больше не только первый по порядку создания.
    """
    stores = await list_active_stores()
    if stores:
        for store in stores:
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
