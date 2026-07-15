import os
import json
import logging
import asyncio
import base64
import requests
from typing import List, Optional, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)

def discover_merchant_id(token: str) -> str:
    """
    Автоматически определяет Merchant ID (ID кабинета) по API-токену.
    Делает GET-запрос к /pointofservices и парсит ID первой точки до знака '_'.
    """
    url = "https://kaspi.kz/shop/api/v2/pointofservices"
    headers = {
        "Content-Type": "application/vnd.api+json",
        "X-Auth-Token": token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                pos_id = data[0].get("id", "")
                if "_" in pos_id:
                    merchant_id = pos_id.split("_")[0]
                    logger.info(f"🎯 Успешно определен Merchant ID: {merchant_id}")
                    return merchant_id
                elif pos_id:
                    return pos_id
        logger.warning(f"Не удалось получить Merchant ID. Статус: {resp.status_code}, Ответ: {resp.text}")
    except Exception as e:
        logger.exception(f"Ошибка при определении Merchant ID: {e}")
    return ""

def _decode_base64_if_needed(val: str) -> str:
    """Декодирует Base64 строку в числовой Kaspi Master SKU, если необходимо."""
    if not val:
        return ""
    val_str = str(val).strip()
    if val_str.isdigit():
        return val_str
    try:
        decoded = base64.b64decode(val_str).decode("utf-8")
        if decoded.isdigit():
            return decoded
    except Exception:
        pass
    return val_str

def _fetch_offers_sync(master_sku: str, city_code: str) -> List[dict]:
    """
    Синхронный запрос предложений товара по конкретному городу.
    """
    url = f"https://kaspi.kz/yml/product-detail/v5/offers?code={master_sku}&c={city_code}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": f"https://kaspi.kz/shop/p/-{master_sku}/?c={city_code}",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Connection": "keep-alive"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                return data.get("offers", data.get("data", {}).get("offers", []))
        else:
            logger.warning(f"Запрос предложений для SKU {master_sku} завершился со статусом {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Исключение при запросе предложений для SKU {master_sku}: {e}")
    return []

async def fetch_competitor_offers(master_sku: str, city_code: str) -> List[dict]:
    """
    Асинхронная обертка для сетевого запроса предложений.
    """
    return await asyncio.to_thread(_fetch_offers_sync, master_sku, city_code)

@dataclass
class DumpingProductConfig:
    sku: str  # Внутренний артикул (merchantSku)
    master_sku: str  # Kaspi Master SKU
    my_current_price: float
    min_price: float
    max_price: float
    step: float = 10.0
    enabled: bool = True
    city_code: str = "750000000"

class DumpingEngine:
    @staticmethod
    def calculate_new_price(config: DumpingProductConfig, my_merchant_id: str, sellers_offers: List[dict]) -> dict:
        """
        Рассчитывает новую цену с учетом конкурентов, лимитов min/max и шага демпинга.
        Реализует защиту от самодемпинга (Self-Dumping Prevention) и случай отсутствия на карточке.
        """
        if not sellers_offers:
            target_price = config.max_price if config.max_price else config.my_current_price
            return {
                "targetPrice": target_price,
                "currentPos": 1,
                "bestCompetitorPrice": None,
                "firstPrice": target_price,
                "action": "SET_MAX_PRICE_SINGLE_SELLER"
            }

        # Очистка и валидация цен предложений
        cleaned_offers = []
        for offer in sellers_offers:
            merchant_id = offer.get("merchantId")
            price_val = offer.get("price")
            if price_val is not None:
                try:
                    price_num = float(str(price_val).replace(" ₸", "").replace(" ", "").strip())
                    cleaned_offers.append({
                        "merchantId": str(merchant_id),
                        "price": price_num
                    })
                except ValueError:
                    continue

        if not cleaned_offers:
            target_price = config.max_price if config.max_price else config.my_current_price
            return {
                "targetPrice": target_price,
                "currentPos": 1,
                "bestCompetitorPrice": None,
                "firstPrice": target_price,
                "action": "SET_MAX_PRICE_SINGLE_SELLER"
            }

        # Сортировка предложений по возрастанию цены
        sorted_offers = sorted(cleaned_offers, key=lambda x: x["price"])
        first_price = sorted_offers[0]["price"]

        # Определение нашей позиции
        my_pos = None
        for idx, offer in enumerate(sorted_offers, start=1):
            if offer.get("merchantId") == str(my_merchant_id):
                my_pos = idx
                break

        # Отфильтровываем конкурентов
        competitors = [o for o in sorted_offers if o.get("merchantId") != str(my_merchant_id)]

        # Если конкурентов нет вообще (мы одни на карточке)
        if not competitors:
            target_price = config.max_price if config.max_price else config.my_current_price
            return {
                "targetPrice": target_price,
                "currentPos": 1,
                "bestCompetitorPrice": None,
                "firstPrice": first_price,
                "action": "SET_MAX_PRICE_SINGLE_SELLER"
            }

        best_competitor_price = competitors[0]["price"]

        # Кейс 1: Нас нет на карточке (встаем в демпинг)
        if my_pos is None:
            calculated_target = best_competitor_price - config.step
            if calculated_target < config.min_price:
                target_price = config.min_price
                action = "REACHED_MIN_PRICE_ENTER_CARD"
            else:
                target_price = calculated_target
                action = "ENTER_CARD"
            
            return {
                "targetPrice": target_price,
                "currentPos": len(sorted_offers) + 1,
                "bestCompetitorPrice": best_competitor_price,
                "firstPrice": first_price,
                "action": action
            }

        # Кейс 2: Мы на 1-м месте
        if my_pos == 1:
            gap = best_competitor_price - config.my_current_price
            if gap > (config.step * 2):
                # Разрыв слишком большой — приподнимаем цену (защита от самодемпинга)
                calculated_target = best_competitor_price - config.step
                if config.max_price:
                    calculated_target = min(calculated_target, config.max_price)
                
                if calculated_target < config.min_price:
                    target_price = config.min_price
                    action = "REACHED_MIN_PRICE_PULL_UP"
                else:
                    target_price = calculated_target
                    action = "PULL_UP_PRICE"
            else:
                # Держим текущую позицию
                target_price = config.my_current_price
                action = "HOLD_FIRST_POS"

            return {
                "targetPrice": target_price,
                "currentPos": 1,
                "bestCompetitorPrice": best_competitor_price,
                "firstPrice": first_price,
                "action": action
            }

        # Кейс 3: Мы на карточке, но не на 1-м месте
        calculated_target = best_competitor_price - config.step
        if calculated_target < config.min_price:
            target_price = config.min_price
            action = "REACHED_MIN_PRICE"
        else:
            target_price = calculated_target
            action = "PRICE_UPDATED"

        return {
            "targetPrice": target_price,
            "currentPos": my_pos,
            "bestCompetitorPrice": best_competitor_price,
            "firstPrice": first_price,
            "action": action
        }

async def run_price_dumping_job(
    token: str,
    configs: dict,
    products: List[dict],
    save_override_func
) -> dict:
    """
    Основной асинхронный воркер демпинга.
    Принимает токен, словарь конфигов демпинга, кэш товаров и функцию сохранения оверрайда.
    """
    merchant_id = discover_merchant_id(token)
    if not merchant_id:
        # Fallback to standard merchant id if discovery failed
        merchant_id = "20186986"
        logger.warning(f"Не удалось получить динамический merchant_id. Используем fallback: {merchant_id}")

    # Фильтруем товары, для которых включен демпинг
    enabled_products = []
    crawl_tasks = []

    for p in products:
        m_sku = p.get("merchantSku")
        sku_key = m_sku if m_sku in configs else p.get("sku")
        
        if not sku_key or sku_key not in configs:
            continue
            
        cfg_data = configs[sku_key]
        if not cfg_data.get("enabled"):
            continue

        # Парсим текущую цену
        price_num = p.get("price_num")
        if price_num is None:
            try:
                price_str = p.get("price", "0").replace(" ₸", "").replace(" ", "").strip()
                price_num = float(price_str)
            except Exception:
                price_num = 0.0

        # Вычисляем Kaspi Master SKU
        raw_kaspi_sku = p.get("kaspiSku") or p.get("sku") or ""
        kaspi_master_sku = _decode_base64_if_needed(raw_kaspi_sku)

        if not kaspi_master_sku:
            logger.warning(f"Пропуск {sku_key}: не найден Kaspi Master SKU.")
            continue

        city_code = cfg_data.get("cityCode") or cfg_data.get("city_code") or "750000000"

        config = DumpingProductConfig(
            sku=sku_key,
            master_sku=kaspi_master_sku,
            my_current_price=float(price_num),
            min_price=float(cfg_data.get("min_price", 0.0)),
            max_price=float(cfg_data.get("max_price", 0.0)),
            step=float(cfg_data.get("dump_step", 10.0)),
            enabled=True,
            city_code=city_code
        )

        enabled_products.append((p, config))
        crawl_tasks.append(fetch_competitor_offers(kaspi_master_sku, city_code))

    if not enabled_products:
        return {
            "success": True,
            "updated_count": 0,
            "message": "Нет товаров с включенным авто-демпингом.",
            "logs": []
        }

    # Запускаем сбор цен конкурентов параллельно
    logger.info(f"Запускаем парсинг предложений для {len(crawl_tasks)} товаров...")
    offers_results = await asyncio.gather(*crawl_tasks)

    updated_count = 0
    log_messages = []

    for (p, config), offers in zip(enabled_products, offers_results):
        sku_key = config.sku
        # Расчет новой цены
        res = DumpingEngine.calculate_new_price(config, merchant_id, offers)
        new_price = res["targetPrice"]

        # Если цена изменилась
        if abs(new_price - config.my_current_price) > 0.01:
            p["price_num"] = int(new_price)
            p["price"] = f"{int(new_price)} ₸"
            updated_count += 1

            min_comp = res["bestCompetitorPrice"] or new_price
            msg = (
                f"Авто-демпинг {sku_key} (город {config.city_code}): "
                f"{int(config.my_current_price)} ₸ -> {int(new_price)} ₸ "
                f"(конкурент: {int(min_comp)} ₸, действие: {res['action']}, позиция: {res['currentPos']})"
            )
            log_messages.append(msg)
            logger.info(msg)

            # Сохраняем оверрайд
            save_override_func(
                sku_key,
                int(new_price),
                p.get("stock", 10),
                p.get("brand"),
                p.get("status")
            )
        else:
            msg = f"Товар {sku_key}: цена {int(config.my_current_price)} ₸ не изменилась (действие: {res['action']}, позиция: {res['currentPos']})"
            logger.debug(msg)

    summary_msg = f"Расчет демпинга завершен. Обновлено цен: {updated_count}."
    return {
        "success": True,
        "updated_count": updated_count,
        "message": summary_msg,
        "logs": log_messages
    }

def generate_xml_feed(merchant_id: str, products: List[dict]) -> bytes:
    """
    Генерирует XML-прайс-лист для Kaspi Catalog Sync.
    """
    from xml.etree.ElementTree import Element, SubElement, tostring
    import datetime

    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    root_el = Element("kaspi_catalog", {
        "date": now_str,
        "xmlns": "http://kaspi.kz/kaspicatalog",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"
    })

    company_el = SubElement(root_el, "company")
    company_el.text = "ИП Твой Мерчант"

    merchant_el = SubElement(root_el, "merchantid")
    merchant_el.text = str(merchant_id)

    offers_el = SubElement(root_el, "offers")

    for p in products:
        sku = p.get("sku")
        if not sku:
            continue

        name = p.get("name") or f"Товар {sku}"
        brand = p.get("brand") or "Barkhan"

        price_num = p.get("price_num")
        if price_num is None:
            try:
                price_str = p.get("price", "0").replace(" ₸", "").replace(" ", "").strip()
                price_num = int(price_str)
            except Exception:
                price_num = 0

        stock = p.get("stock", 10)
        status = p.get("status", "Активен")
        available = "yes" if stock > 0 and status == "Активен" else "no"

        offer_el = SubElement(offers_el, "offer", {"sku": str(sku)})
        
        model_el = SubElement(offer_el, "model")
        model_el.text = str(name)

        brand_el = SubElement(offer_el, "brand")
        brand_el.text = str(brand)

        avail_parent = SubElement(offer_el, "availabilities")
        SubElement(avail_parent, "availability", {
            "available": available,
            "storeId": f"{merchant_id}_001",
            "stock": str(stock)
        })

        price_el = SubElement(offer_el, "price")
        price_el.text = str(price_num)

    xml_str = tostring(root_el, encoding="utf-8")
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + xml_str
