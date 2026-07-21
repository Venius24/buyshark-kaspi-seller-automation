import concurrent.futures
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from backend.kaspi_wrapper import KaspiAPI
from backend.models import PackOrderRequest
from backend.state_helpers import _require_token

router = APIRouter(prefix="/api", tags=["orders"])

@router.get("/orders")
def get_orders(
    state: str = Query("NEW"),
    status: str = Query("APPROVED_BY_BANK"),
    limit: int = Query(20),
    assembled: Optional[bool] = None,
):
    """Получить заказы с полной цепочкой."""
    token = _require_token()
    try:
        orders = KaspiAPI.get_orders_with_products(token, state=state, status=status, limit=limit, assembled=assembled)
        return {"success": True, "orders": orders, "count": len(orders)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/orders/pack")
def pack_order(req: PackOrderRequest):
    """Перевести заказ в новый статус или упаковать."""
    token = _require_token()
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
            KaspiAPI.assemble_cargo(token, code, qty)
            return {"success": True, "message": f"Заказ {code} успешно упакован и готов к передаче."}
            
        raise Exception("Заказ не найден в статусах NEW или ACCEPTED_BY_MERCHANT.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/orders/pack-all")
def pack_all_orders():
    """Принять все новые заказы и упаковать все заказы на сборке."""
    token = _require_token()
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
                    executor.submit(KaspiAPI.assemble_cargo, token, o["id"], o["quantity"]): o
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
def get_transfers(filter: str = Query("ALL")):
    """Получить список передач."""
    token = _require_token()
    try:
        transfers = KaspiAPI.get_transfers(token, delivery_filter=filter)
        return {"success": True, "transfers": transfers, "count": len(transfers)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/cities")
def get_cities():
    """Получить справочник городов."""
    token = _require_token()
    try:
        cities = KaspiAPI.get_cities(token)
        return {"success": True, "cities": cities}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
