import asyncio
import json
import logging
import time
from typing import List, Dict, Any
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

class ConnectionManager:
    """
    Менеджер WebSocket-подключений для бродкаста событий в реальном времени.
    """
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"📡 [WebSocket] Новое подключение клиентской панели. Всего клиентов: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"📡 [WebSocket] Отключение клиента. Осталось клиентов: {len(self.active_connections)}")

    async def broadcast(self, event_type: str, payload: Dict[str, Any]):
        """
        Бродкастит JSON-сообщение всем подключенным клиентам.
        """
        if not self.active_connections:
            return

        message = {
            "type": event_type,
            "timestamp": time.time(),
            "data": payload
        }
        
        dead_connections = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Ошибка отправки WS сообщения: {e}")
                dead_connections.append(connection)

        for dead in dead_connections:
            self.disconnect(dead)

# Глобальный экземпляр менеджера
telemetry_manager = ConnectionManager()

def emit_telemetry_sync(event_type: str, payload: Dict[str, Any]):
    """
    Синхронный помощник для отправки событий из обычных потоков/функций.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(telemetry_manager.broadcast(event_type, payload), loop)
    except Exception:
        pass
