"""Проекции локальных загрузчиков в поисковые документы."""

import json

from backend.assistant.rag.base import Document


def build_documents(products: list[dict], daily: list[dict], war: list[dict], logs: list[dict],
                    drafts: list[dict] | None = None,
                    groups: dict | list[dict] | None = None) -> list[Document]:
    result: list[Document] = []
    active_groups = []
    groups_by_sku = {}
    for group in sorted(groups.values() if isinstance(groups, dict) else groups or [],
                        key=lambda g: str(g.get("group_id") or "")):
        if not group.get("is_active"):
            continue
        linked = group.get("linked_skus") or []
        if isinstance(linked, str):
            try:
                linked = json.loads(linked)
            except (ValueError, TypeError):
                linked = []
        linked = list(dict.fromkeys(str(sku) for sku in linked if sku is not None and str(sku))) if isinstance(linked, list) else []
        active_groups.append((group, linked))
        for sku in linked:
            groups_by_sku.setdefault(sku, []).append(group)

    def group_annotation(sku, metadata):
        matches = groups_by_sku.get(sku, [])
        if not matches:
            return ""
        group = matches[0]
        name = str(group.get("group_name") or "")
        metadata.update(master_group=name, master_group_id=group["group_id"])
        if len(matches) > 1:
            metadata["master_group_count"] = len(matches)
        return f"мастер-группа {name[:60]} "

    products_by_sku = {}
    for item in products:
        merchant_sku = str(item.get("merchantSku") or "")
        sku = merchant_sku or str(item.get("sku") or "")
        if not sku:
            continue
        products_by_sku[sku] = item
        cfg = item.get("dumping_config") or {}
        metadata = {"merchantSku": merchant_sku or None, "kaspiSku": item.get("kaspiSku") or item.get("sku"),
                    "brand": item.get("brand"), "status": item.get("status"),
                    "stock": item.get("stock"), "price": item.get("price_num"),
                    "has_image": bool(item.get("image"))}
        if item.get("url"):
            metadata["url"] = str(item["url"])
        text = (group_annotation(sku, metadata) +
                f"Товар {str(item.get('name') or '')[:160]} артикул {merchant_sku} "
                f"kaspiSku {metadata['kaspiSku'] or ''} "
                f"ссылка {metadata.get('url', '')} бренд {metadata['brand'] or ''} "
                f"статус {metadata['status'] or ''} остаток {metadata['stock']} "
                f"цена {metadata['price']} картинка {'есть' if metadata['has_image'] else 'нет'} "
                f"минимальная цена {cfg.get('min_price')}")
        result.append(Document(f"product:{sku}", "product", sku, text[:800], metadata))
    for index, draft in enumerate(drafts or []):
        if draft.get("status") not in ("approved", "submitted"):
            continue
        payload = draft.get("draft_payload") or {}
        sku = str(payload.get("sku") or "")
        if not sku:
            continue
        product = products_by_sku.get(sku) or {}
        url = product.get("url")
        metadata = {"merchantSku": sku, "brand": payload.get("brand_name"),
                    "category": payload.get("category_name"), "status": draft["status"]}
        if url:
            metadata["url"] = str(url)
        features = payload.get("features") or {}
        feature_meta = payload.get("feature_meta") or {}
        feature_text = []
        for code, value in features.items():
            if value is None or value == "" or value == []:
                continue
            name = (feature_meta.get(code) or {}).get("name") or code
            rendered = ", ".join(str(part) for part in value) if isinstance(value, list) else str(value)
            feature_text.append(f"{name}: {rendered}")
        text = (group_annotation(sku, metadata) +
                f"Карточка артикул {sku} {product.get('name') or draft.get('raw_title') or ''} "
                f"категория {payload.get('category_name') or ''} бренд {payload.get('brand_name') or ''} "
                f"характеристики {'; '.join(feature_text)} описание {payload.get('description') or ''} "
                f"ссылка {url or ''}")
        result.append(Document(f"card:{draft.get('id') or index}", "card", sku, text[:1500], metadata))
    for group, linked in active_groups:
        group_id = group["group_id"]
        metadata = {key: group.get(key) for key in
                    ("group_id", "group_name", "shared_stock", "stop_qty")}
        metadata["linked_count"] = len(linked)
        members = ", ".join(linked[:40])
        if len(linked) > 40:
            members += f" и ещё {len(linked) - 40}"
        names = [str(products_by_sku[sku].get("name") or "")[:160]
                 for sku in linked if sku in products_by_sku][:10]
        text = (f"Мастер-группа {group.get('group_name') or ''}: "
                f"общий остаток {group.get('shared_stock')}, стоп-остаток {group.get('stop_qty')}, "
                f"число артикулов {len(linked)}; артикулы {members}; товары {'; '.join(names)}")
        result.append(Document(f"master_group:{group_id}", "master_group", str(group_id), text[:1500], metadata))
    for item in daily:
        day = str(item.get("date") or "")
        if day:
            result.append(Document(f"sales_day:{day}", "sales_day", None,
                                   f"Продажи {day}: заказов {item.get('orders')}, выручка {item.get('revenue')} тенге"))
    for item in war:
        sku = str(item.get("sku") or "")
        if not sku:
            continue
        result.append(Document(f"price_war:{sku}", "price_war", sku,
                               (f"Ценовая война {sku} {item.get('name') or ''}: наша позиция {item.get('our_rank')}, "
                                f"наша цена {item.get('our_price')}, лучшая цена конкурента "
                                f"{item.get('best_competitor_price')}, конкурент {item.get('best_competitor_name') or ''}")[:800]))
    for index, item in enumerate(logs):
        sku = item.get("sku")
        sku = str(sku) if sku is not None else None
        safe = {key: item.get(key) for key in ("sku", "timestamp", "created_at", "action", "reason", "status",
                                               "old_price", "new_price", "message") if key in item}
        result.append(Document(f"dumper_log:{index}", "dumper_log", sku,
                               "Лог демпинга " + " ".join(f"{key}: {value}" for key, value in safe.items())[:800]))
    return result
