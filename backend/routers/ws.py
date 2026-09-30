from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.dumper.telemetry import telemetry_manager
from backend.db.database import AsyncSessionLocal
from backend.security.store_context import resolve_ws_store_id

router = APIRouter(tags=["telemetry"])

@router.websocket("/api/ws/telemetry")
async def websocket_endpoint(websocket: WebSocket):
    # Своя сессия, закрывается сразу после резолва store_id — Depends(get_db)
    # здесь держал бы соединение открытым (idle in transaction) на всё время
    # жизни WS-подключения (часы, пока открыта вкладка браузера), а не на
    # время запроса. При достаточном числе одновременных вкладок это выедало
    # пул (10+20=30) целиком и клало ВСЕ фоновые воркеры, включая чат-бота
    # (инцидент 2026-08-22).
    async with AsyncSessionLocal() as db:
        store_id = await resolve_ws_store_id(websocket, db)
    if not await telemetry_manager.connect(websocket, store_id):
        return
    try:
        while True:
            # Ожидаем любые входные сообщения (например, ping/pong от клиента)
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        telemetry_manager.disconnect(websocket, store_id)
    except Exception:
        telemetry_manager.disconnect(websocket, store_id)
