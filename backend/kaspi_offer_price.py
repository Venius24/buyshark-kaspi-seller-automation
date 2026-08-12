"""
Извлечение реальной цены оффера из ответа Kaspi BFF (bff/offer-view/list).

Плоское поле offer["price"] — денормализованный кэш, который у части офферов
отдаёт 0, хотя цена реально назначена (замерено вживую на проде 2026-07-29:
35 из 100 офферов реального каталога buyshark). Судя по всему, кэш не
переиндексируется сразу после ON_SALE / ручной реактивации оффера в кабинете.

Настоящая цена в этом случае лежит в cityPrices / minPrice / rangePrice /
allCityPrices — перебираем их по очереди.

Вынесено в отдельный модуль 2026-08-12: правило уже было выучено в
kaspi_wrapper.sync_merchant_assortment, но offer_sync._fetch_kaspi_state
читала offer["price"] наивно — и записала на Kaspi цену 0 по двум карточкам
(H422-0002-7, H422-0002-9), после чего Kaspi снял их с продажи с причиной
"не указан в прайс-листе". Один источник правды, чтобы урок не пришлось
учить в третий раз.
"""
from typing import Optional


def _positive_int(value) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return None


def extract_offer_price(offer: dict) -> Optional[int]:
    """
    Реальная цена оффера или None.

    Именно None, а НЕ 0: "цену прочитать не удалось" и "цена равна нулю" —
    принципиально разные вещи. Ноль, попавший в payload
    pricefeed/upload/merchant/process, снимает карточку с продажи.
    """
    if not isinstance(offer, dict):
        return None

    price = _positive_int(offer.get("price"))
    if price:
        return price

    city_prices = offer.get("cityPrices")
    if isinstance(city_prices, list):
        for entry in city_prices:
            if isinstance(entry, dict):
                price = _positive_int(entry.get("value"))
                if price:
                    return price

    price = _positive_int(offer.get("minPrice"))
    if price:
        return price

    range_price = offer.get("rangePrice")
    if isinstance(range_price, dict):
        price = _positive_int(range_price.get("MIN"))
        if price:
            return price

    all_city_prices = offer.get("allCityPrices")
    if isinstance(all_city_prices, dict):
        for entry in all_city_prices.values():
            if isinstance(entry, dict):
                price = _positive_int(entry.get("price"))
                if price:
                    return price

    return None
