import os
import json
import asyncio
import logging
from backend.kaspi_wrapper import KaspiAPI
from backend.state_helpers import _get_token, _apply_overrides

logger = logging.getLogger(__name__)

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
            token = _get_token()
            if not token:
                logger.warning("⚠️ [Products Sync Worker] Токен авторизации не найден. Пропуск цикла автосинхронизации.")
            else:
                logger.info("🔄 [Products Sync Worker] Запуск планового обновления товаров из Kaspi API...")
                
                # Запускаем синхронный запрос к Kaspi API в отдельном потоке
                products = await asyncio.to_thread(KaspiAPI.sync_merchant_assortment, token)
                
                if products:
                    products = _apply_overrides(products)
                    products_file = "states/products.json"
                    os.makedirs("states", exist_ok=True)
                    
                    with open(products_file, "w", encoding="utf-8") as f:
                        json.dump(products, f, ensure_ascii=False, indent=2)
                        
                    logger.info(f"✅ [Products Sync Worker] Успешно обновлено {len(products)} товаров из Kaspi API.")
                else:
                    logger.warning("⚠️ [Products Sync Worker] Получен пустой список товаров из Kaspi API.")
        except Exception as e:
            logger.error(f"❌ [Products Sync Worker] Ошибка во время автосинхронизации товаров: {e}")
            
        await asyncio.sleep(interval_seconds)
