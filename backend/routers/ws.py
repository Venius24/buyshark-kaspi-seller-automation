from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from backend.dumper.telemetry import telemetry_manager

router = APIRouter(tags=["telemetry"])

@router.websocket("/api/ws/telemetry")
async def websocket_endpoint(websocket: WebSocket):
    await telemetry_manager.connect(websocket)
    try:
        while True:
            # Ожидаем любые входные сообщения (например, ping/pong от клиента)
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        telemetry_manager.disconnect(websocket)
    except Exception:
        telemetry_manager.disconnect(websocket)
