import datetime
import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from backend.kaspi_wrapper import KaspiAPI
from backend.models import ProductTagsRequest, UpdateProductRequest
from backend.state_helpers import (
    _save_override, _apply_overrides, _load_products, _save_products, _purge_stale_overrides,
    _list_store_tags, _load_product_tags, _save_product_tags,
)
from backend.security.store_context import StoreContext
from backend.security.store_roles import require_section, require_store_owner
from backend.dumper import generate_xml_feed
from backend.product_name_overrides import resolve_product_name

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["products"])

# Префикс /api общий с orders.py, поэтому охрана раздела поэндпоинтная.
# XML-фид (/feed) сознательно оставлен владельческим: это декларация всего
# каталога наружу, цена ошибки — снятые с продажи офферы (HANDOFF §3.67).
_products = require_section("products")
_products_write = require_section("products", min_role="MANAGER")


def _tag_key(product: dict) -> str:
    """Артикул, под которым у товара живут метки."""
    return str(product.get("merchantSku") or product.get("sku") or "").strip()

@router.get("/products")
def get_products(
    page: int = Query(1),
    page_size: int = Query(20),
    search: str = Query(""),
    status: str = Query("active"),
    dumping_only: bool = Query(False),
    dumping_status_filter: str = Query(""),
    # Разрезы вкладки авто-демпинга (см. backend/services/dumping_filters.py).
    # Все комбинируются через И; пустые/None — не фильтруют.
    dumping_state: str = Query("", description="ON | OFF | NO_CONFIG | OFF_WITH_RIVALS"),
    price_state: str = Query("", description="AT_MIN | AT_MAX | IN_RANGE | NO_LIMITS"),
    rank_state: str = Query("", description="LEADER | NOT_LEADER | NO_DATA"),
    min_competitors: Optional[int] = Query(None, ge=0),
    max_competitors: Optional[int] = Query(None, ge=0),
    ctx: StoreContext = Depends(_products),
):
    """Получить список товаров из кэша или Kaspi API с конфигурациями и статусами демпинга."""
    token = ctx.token

    all_products = _load_products(ctx.store_id)
    # Если кэш содержит тестовые фейковые товары SKU-001 — отбрасываем
    if any(p.get("sku") in ["SKU-001", "SKU-002"] for p in all_products):
        all_products = []
    else:
        all_products = _apply_overrides(all_products, store_id=ctx.store_id)

    if not all_products:
        try:
            all_products = KaspiAPI.sync_merchant_assortment(token, store_id=ctx.store_id)
            all_products = _apply_overrides(all_products, store_id=ctx.store_id)
            _save_products(all_products, ctx.store_id)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Не удалось получить товары: {e}")

    # Обогащаем товары состоянием демпинга: эффективный конфиг (свой +
    # развёрнутый поверх него конфиг активной Мастер-Группы — ровно тем же
    # кодом, что и у движка), где цена относительно коридора min/max, и
    # расклад конкурентов из последнего скана радара.
    #
    # Матч конфига — сперва по merchantSku, потом по sku. Раньше здесь был
    # матч ТОЛЬКО по sku, а конфиги пишутся по merchantSku: на проде 374 из
    # 377 конфигов были невидимы для этой ручки — товары показывались как
    # "демпинг выключен", фильтр «только с демпингом» отдавал пустоту, и
    # успешный импорт из PriceFeed выглядел как несостоявшийся.
    from backend.state_helpers import (
        _load_dumping_configs,
        _load_master_stock_groups,
        _load_price_war_snapshot,
    )
    from backend.services import dumping_filters

    dumping_configs = _load_dumping_configs(store_id=ctx.store_id)
    master_groups = _load_master_stock_groups(store_id=ctx.store_id)
    price_war_items = _load_price_war_snapshot(ctx.store_id)

    dumping_filters.annotate_products(
        all_products, dumping_configs, master_groups, price_war_items
    )

    # Метки товара. Ключ — артикул продавца: именно он приходит в составе
    # заказа из Kaspi Order API, по нему же печать находит теги для этикетки.
    product_tags = _load_product_tags(ctx.store_id)
    if product_tags:
        for p in all_products:
            p["tags"] = product_tags.get(_tag_key(p), [])

    if status and status != "all":
        target_status = "Активен" if status == "active" else ("Архив" if status == "archive" else status)
        filtered = [p for p in all_products if p.get("status") == target_status]
    else:
        filtered = list(all_products)


    if search:
        q = search.lower()
        filtered_list = []
        for p in filtered:
            match_fields = [
                p.get("name"),
                p.get("sku"),
                p.get("merchantSku"),
                p.get("kaspiSku"),
                p.get("brand"),
                p.get("id")
            ]
            if any(q in str(field).lower() for field in match_fields if field is not None):
                filtered_list.append(p)
        filtered = filtered_list

    # Счётчики срезов считаем ДО применения самих демпинг-фильтров (поиск и
    # активен/архив уже учтены) — иначе в выпадающих списках стояли бы числа
    # уже отфильтрованной выборки, и пустой результат выглядел бы поломкой.
    facets = dumping_filters.facet_counts(filtered)
    comp_min, comp_max = dumping_filters.competitors_range(filtered)

    # Фильтр только активных товаров в авто-демпинге. Теперь учитывает и
    # групповой демпинг Мастер-Групп: такие товары движок реально демпингует,
    # и прятать их из среза «только с демпингом» было враньём.
    if dumping_only:
        filtered = [p for p in filtered if p.get("dumping_state") == dumping_filters.DUMPING_STATE_ON]

    filtered = dumping_filters.apply_dumping_filters(
        filtered,
        dumping_state=dumping_state,
        price_state=price_state,
        rank_state=rank_state,
        dumping_status_filter=dumping_status_filter,
        min_competitors=min_competitors,
        max_competitors=max_competitors,
    )

    total_count = len(filtered)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    start = (page - 1) * page_size
    sliced = filtered[start : start + page_size]

    return {
        "success": True,
        "products": sliced,
        "total_count": total_count,
        "total_pages": total_pages,
        "facets": facets,
        "competitors_range": {"min": comp_min, "max": comp_max},
    }

@router.get("/products/tags")
def list_product_tags(ctx: StoreContext = Depends(_products)):
    """Все метки магазина с числом товаров — для подсказок при вводе."""
    return {"success": True, "tags": _list_store_tags(ctx.store_id)}


@router.put("/products/{sku}/tags")
def set_product_tags(sku: str, req: ProductTagsRequest, ctx: StoreContext = Depends(_products_write)):
    """Заменить набор меток товара целиком."""
    if len(req.tags) > 20:
        raise HTTPException(status_code=400, detail="Слишком много меток на одном товаре (максимум 20)")
    saved = _save_product_tags(ctx.store_id, sku, req.tags)
    return {"success": True, "sku": sku, "tags": saved}


@router.post("/products/update")
def update_product(req: UpdateProductRequest, ctx: StoreContext = Depends(_products_write)):
    """Обновить цену и остаток товара (сохранить в overrides и применить в Kaspi MC)."""
    token = ctx.token
    products = _load_products(ctx.store_id)
    if not products:
        raise HTTPException(status_code=404, detail="Список товаров не найден. Загрузите товары сначала.")

    try:
        _save_override(req.sku, req.price, req.stock, req.brand, req.status, store_id=ctx.store_id)

        updated = False
        target_p = None
        for p in products:
            if p.get("sku") == req.sku:
                p["price_num"] = req.price
                p["price"] = f"{req.price} ₸"
                p["stock"] = req.stock
                if req.brand:
                    p["brand"] = req.brand
                p["status"] = req.status or ("Активен" if req.stock > 0 else "Архив")
                target_p = p
                updated = True
                break

        if not updated:
            raise HTTPException(status_code=404, detail=f"Товар с SKU {req.sku} не найден.")

        _save_products(products, ctx.store_id)

        # Моментальный push в Kaspi MC
        from backend.dumper.executor import _load_mc_cookies, RateLimitExecutorWorker
        from backend.dumper.config import discover_merchant_id, _decode_base64_if_needed

        cookies = _load_mc_cookies(store_id=ctx.store_id)
        mc_info = ""
        merchant_id = discover_merchant_id(token) if (cookies and "mc-session" in cookies and "mc-sid" in cookies) else None
        if merchant_id:
            master_sku = target_p.get("kaspiSku") or target_p.get("sku")
            decoded_master = _decode_base64_if_needed(master_sku)

            store_full_id = target_p.get("storeId") or f"{merchant_id}_001"
            store_suffix = store_full_id.split("_")[1] if "_" in store_full_id else "001"

            success, val = RateLimitExecutorWorker._update_kaspi_price_sync(
                merchant_uid=merchant_id,
                sku=target_p.get("merchantSku") or req.sku,
                master_sku=decoded_master,
                model_name=resolve_product_name(target_p.get("merchantSku") or req.sku, target_p.get("name")) or "",
                new_price=int(req.price),
                stock=int(req.stock),
                store_id_suffix=store_suffix,
                city_id="750000000",
                internal_store_id=ctx.store_id
            )
            if success:
                mc_info = f" и успешно отправлен в Kaspi MC (ID: {val})"
            else:
                mc_info = f" (локально, ошибка MC: {val})"
        elif cookies and "mc-session" in cookies and "mc-sid" in cookies:
            # НЕ подставляем чужой merchant_id (раньше был хардкод "20186986" —
            # реальный ID Barkhan, из-за чего push для другого магазина уходил
            # под его личностью и ловил 403 от Kaspi).
            mc_info = " (локально, не удалось определить Merchant ID)"
        else:
            mc_info = " (локально, куки MC отсутствуют)"

        return {"success": True, "message": f"Товар {req.sku} успешно обновлен{mc_info}."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/products/refresh")
def refresh_products(ctx: StoreContext = Depends(_products_write)):
    """Принудительно обновить кеш товаров из Kaspi API."""
    try:
        fetched_at = datetime.datetime.utcnow()
        products = KaspiAPI.sync_merchant_assortment(ctx.token, store_id=ctx.store_id)
        # Тот же барьер свежести, что и в products_sync_worker: только что
        # выкачанную из Kaspi цену не перекрываем вчерашним оверрайдом.
        products = _apply_overrides(products, store_id=ctx.store_id, fresh_from_kaspi_at=fetched_at)
        _save_products(products, ctx.store_id)
        _purge_stale_overrides(ctx.store_id, fetched_at)
        return {"success": True, "count": len(products), "message": f"Обновлено {len(products)} товаров."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/feed")
def get_xml_feed(ctx: StoreContext = Depends(require_store_owner)):
    """Сгенерировать XML-фид для Kaspi Catalog Sync (магазина текущего пользователя).

    Раньше был вообще без авторизации (читал states/session.json напрямую) —
    теперь, как и остальные эндпоинты этого роутера, резолвит контекст через
    dual-auth (см. security/store_context.py::get_store_context).

    ВЫКЛЮЧЕНО ПО УМОЛЧАНИЮ (XML_FEED_ENDPOINT_ENABLED=1, чтобы включить) —
    прайс-фид это декларация ВСЕГО каталога: оффер, которого в файле нет,
    Kaspi снимает с продажи с причиной «не указан в прайс-листе». Ровно так
    у владельца однажды слегло ~798 карточек из-за файла на два SKU (см.
    HANDOFF.md §3.67). Ручка отдаёт содержимое кэша `states/products.json`
    без всякой проверки полноты — усечённый кэш здесь неотличим от полного
    каталога. Пока не готов сборщик снимка с инвариантом полноты
    (`backend/dumper/feed_snapshot.py`), отдавать этот файл наружу опасно.
    """
    if os.getenv("XML_FEED_ENDPOINT_ENABLED", "0") != "1":
        raise HTTPException(
            status_code=503,
            detail=(
                "XML-фид временно отключён: файл без проверки полноты снимает "
                "с продажи всё, чего в нём нет. Включается XML_FEED_ENDPOINT_ENABLED=1."
            ),
        )
    products = _load_products(ctx.store_id)
    xml_data = generate_xml_feed(ctx.merchant_id, products)
    return Response(content=xml_data, media_type="application/xml")
