import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query

from backend.state_helpers import _list_sales_orders_since, resolve_external_code
from backend.routers.public_api.auth import get_api_key_user

router = APIRouter(prefix="/api/v1/public", tags=["public-api"])


def _serialize_order(o: dict, store_id: str) -> dict:
    entries = []
    for e in o.get("entries") or []:
        sku = e.get("sku")
        merchant_sku = e.get("merchantSku")
        entries.append({
            "sku": sku,
            "merchant_sku": merchant_sku,
            "external_code": resolve_external_code(store_id, sku, merchant_sku),
            "name": e.get("name"),
            "qty": e.get("qty"),
            "total_price": e.get("totalPrice"),
        })
    return {
        "order_internal_id": o.get("order_internal_id"),
        "order_code": o.get("order_code"),
        "created_at": o.get("created_at").isoformat() if o.get("created_at") else None,
        "total_price": o.get("total_price"),
        "status": o.get("status"),
        "state": o.get("state"),
        "entries": entries,
    }


@router.get("/orders")
def get_public_orders(
    since: Optional[str] = Query(None, description="ISO-дата (created_at) последнего полученного заказа — отдаются только более новые."),
    limit: int = Query(100, ge=1, le=500),
    auth_user: dict = Depends(get_api_key_user),
):
    """
    [Public API 1С/ERP] Завершённые заказы (см. sales_orders /
    sales_sync_worker.py) ТОЛЬКО того магазина, которому принадлежит
    предъявленный API-ключ. Курсор ?since= — для fallback-доскана на случай
    пропущенных webhook-событий order.completed (см. webhook_dispatcher.py).

    ЧЕРНОВАЯ схема: комиссия Kaspi, НДС и статус выплаты сюда намеренно не
    включены — эти поля должны пройти согласование с бухгалтером/1С-интегратором
    до того, как станут частью финального контракта.
    """
    store_id = auth_user["store_id"]

    since_dt = None
    if since:
        try:
            since_dt = datetime.datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=400, detail="Параметр since должен быть ISO-датой, например 2026-08-20T00:00:00.")

    orders = _list_sales_orders_since(store_id, since=since_dt, limit=limit)
    data = [_serialize_order(o, store_id) for o in orders]

    return {"success": True, "count": len(data), "data": data}
