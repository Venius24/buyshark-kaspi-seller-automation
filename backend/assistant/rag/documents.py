"""Проекции локальных загрузчиков в поисковые документы."""

from backend.assistant.rag.base import Document


def build_documents(products: list[dict], daily: list[dict], war: list[dict], logs: list[dict]) -> list[Document]:
    result: list[Document] = []
    for item in products:
        sku = str(item.get("merchantSku") or item.get("sku") or "")
        if not sku:
            continue
        cfg = item.get("dumping_config") or {}
        text = (f"Товар {sku} {item.get('name') or ''} бренд {item.get('brand') or ''} "
                f"остаток {item.get('stock')} цена {item.get('price_num')} "
                f"минимальная цена {cfg.get('min_price')}")
        result.append(Document(f"product:{sku}", "product", sku, text[:800]))
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
