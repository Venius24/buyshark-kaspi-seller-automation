from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.dumper.telemetry import telemetry_manager
from backend.db.database import get_db
from backend.security.store_context import resolve_ws_store_id

router = APIRouter(tags=["telemetry"])

@router.websocket("/api/ws/telemetry")
async def websocket_endpoint(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    store_id = await resolve_ws_store_id(websocket, db)
    await telemetry_manager.connect(websocket, store_id)
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
