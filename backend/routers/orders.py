import concurrent.futures
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from backend.kaspi_wrapper import KaspiAPI
from backend.models import PackOrderRequest
from backend.security.store_context import StoreContext, get_store_context

router = APIRouter(prefix="/api", tags=["orders"])

@router.get("/orders/packaging/tabs")
def get_packaging_tabs(ctx: StoreContext = Depends(get_store_context)):
    """Разбор заказов по 3 вкладкам: На сборке, Ожидает курьера, Переданы на доставку (JSON:API benchmark)."""
    token = ctx.token
    try:
        # Общий кэш (TTL 45c, см. kaspi_wrapper.py) — тот же тяжёлый обход
        # дёргают ещё routers/stock_groups.py и 30с-тик в stock_sync_worker.py.
        return KaspiAPI.get_all_packaging_tabs_cached(token)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/orders/packaging/by-prefix")
def get_packaging_orders_by_prefix(prefix: str = Query("T219-0005"), ctx: StoreContext = Depends(get_store_context)):
    """Получить заказы на сборке/в упаковке по префиксу артикула товара (например T219-0005, T219-0005-1,2...)."""
    token = ctx.token
    try:
        return KaspiAPI.get_packaging_orders_by_sku_prefix(token, sku_prefix=prefix)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/orders")
def get_orders(
    state: str = Query("NEW"),
    status: str = Query("APPROVED_BY_BANK"),
    limit: int = Query(20),
    assembled: Optional[bool] = None,
    ctx: StoreContext = Depends(get_store_context),
):
    """Получить заказы с полной цепочкой."""
    token = ctx.token
    try:
        orders = KaspiAPI.get_orders_with_products(token, state=state, status=status, limit=limit, assembled=assembled)
        return {"success": True, "orders": orders, "count": len(orders)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/orders/pack")
def pack_order(req: PackOrderRequest, ctx: StoreContext = Depends(get_store_context)):
    """Перевести заказ в новый статус или упаковать."""
    token = ctx.token
    try:
        new_orders = KaspiAPI.get_orders_with_products(token, state="NEW", status="APPROVED_BY_BANK", limit=50)
        order_item = next((o for o in new_orders if o["internalId"] == req.order_id), None)
        
        if order_item:
            KaspiAPI.update_order_status(token, req.order_id, "ACCEPTED_BY_MERCHANT")
            return {"success": True, "message": f"Заказ {order_item['id']} принят в обработку."}
            
        assembly_orders = KaspiAPI.get_orders_with_products(token, state="KASPI_DELIVERY", status="ACCEPTED_BY_MERCHANT", limit=50, assembled=False)
        order_item = next((o for o in assembly_orders if o["internalId"] == req.order_id), None)
        
        if order_item:
            code = order_item["id"]
            qty = order_item["quantity"]
            KaspiAPI.assemble_cargo(token, code, qty, store_id=ctx.store_id)
            return {"success": True, "message": f"Заказ {code} успешно упакован и готов к передаче."}
            
        raise Exception("Заказ не найден в статусах NEW или ACCEPTED_BY_MERCHANT.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/orders/pack-all")
def pack_all_orders(ctx: StoreContext = Depends(get_store_context)):
    """Принять все новые заказы и упаковать все заказы на сборке."""
    token = ctx.token
    try:
        new_orders = KaspiAPI.get_orders_with_products(token, state="NEW", status="APPROVED_BY_BANK", limit=50)
        accepted_count = 0
        for o in new_orders:
            try:
                KaspiAPI.update_order_status(token, o["internalId"], "ACCEPTED_BY_MERCHANT")
                accepted_count += 1
            except Exception as e:
                print(f"[API] Accept failed for {o['id']}: {e}")
                
        assembly_orders = KaspiAPI.get_orders_with_products(token, state="KASPI_DELIVERY", status="ACCEPTED_BY_MERCHANT", limit=50, assembled=False)
        non_assembled = assembly_orders
        
        assembled_count = 0
        if non_assembled:
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = {
                    executor.submit(KaspiAPI.assemble_cargo, token, o["id"], o["quantity"], ctx.store_id): o
                    for o in non_assembled
                }
                for future in concurrent.futures.as_completed(futures):
                    order = futures[future]
                    try:
                        future.result()
                        assembled_count += 1
                    except Exception as ex:
                        print(f"[API] Cargo assembly failed for order {order['id']}: {ex}")
                        
        msg = f"Принято новых заказов: {accepted_count}. Упаковано заказов: {assembled_count}."
        return {"success": True, "packed": accepted_count + assembled_count, "message": msg}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/transfers")
def get_transfers(filter: str = Query("ALL"), ctx: StoreContext = Depends(get_store_context)):
    """Получить список передач."""
    token = ctx.token
    try:
        transfers = KaspiAPI.get_transfers(token, delivery_filter=filter)
        return {"success": True, "transfers": transfers, "count": len(transfers)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/cities")
def get_cities(ctx: StoreContext = Depends(get_store_context)):
    """Получить справочник городов."""
    token = ctx.token
    try:
        cities = KaspiAPI.get_cities(token)
        return {"success": True, "cities": cities}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
