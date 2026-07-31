import asyncio
import json
import logging
import time
from typing import List, Dict, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# Ссылка на главный event loop, сохраняемая при старте приложения
# (backend/main.py::startup_event). emit_telemetry_sync() вызывается не
# только с самого лупа, но и из рабочих потоков (asyncio.to_thread) — там
# asyncio.get_event_loop() луп не находит и падает в except (сообщение
# молча терялось бы). Явная ссылка на нужный луп нужна именно для этого.
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _main_loop
    _main_loop = loop

class ConnectionManager:
    """
    Менеджер WebSocket-подключений для бродкаста событий в реальном времени.

    Phase 6: подключения сгруппированы по store_id — клиент стора A не видит
    события стора B. store_id=None — legacy/общий канал (клиент без JWT или
    без резолвящегося стора, либо событие от кода, ещё не мигрировавшего на
    per-store вызовы), сохраняет поведение "один канал на всех", как было
    до Phase 6.
    """
    def __init__(self):
        self.active_connections: Dict[Optional[str], List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, store_id: Optional[str] = None):
        await websocket.accept()
        self.active_connections.setdefault(store_id, []).append(websocket)
        total = sum(len(v) for v in self.active_connections.values())
        logger.info(f"📡 [WebSocket] Новое подключение клиентской панели (store={store_id}). Всего клиентов: {total}")

    def disconnect(self, websocket: WebSocket, store_id: Optional[str] = None):
        connections = self.active_connections.get(store_id)
        if connections and websocket in connections:
            connections.remove(websocket)
            if not connections:
                del self.active_connections[store_id]
            total = sum(len(v) for v in self.active_connections.values())
            logger.info(f"📡 [WebSocket] Отключение клиента (store={store_id}). Осталось клиентов: {total}")

    async def broadcast(self, event_type: str, payload: Dict[str, Any], store_id: Optional[str] = None):
        """
        Бродкастит JSON-сообщение клиентам, подключенным под тем же store_id.
        """
        connections = self.active_connections.get(store_id)
        if not connections:
            return

        message = {
            "type": event_type,
            "timestamp": time.time(),
            "data": payload
        }

        dead_connections = []
        for connection in connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.warning(f"Ошибка отправки WS сообщения: {e}")
                dead_connections.append(connection)

        for dead in dead_connections:
            self.disconnect(dead, store_id)

# Глобальный экземпляр менеджера
telemetry_manager = ConnectionManager()

def emit_telemetry_sync(event_type: str, payload: Dict[str, Any], store_id: Optional[str] = None):
    """
    Синхронный помощник для отправки событий из обычных потоков/функций
    (в т.ч. из asyncio.to_thread — отсюда явный _main_loop, а не
    get_event_loop(), который в рабочем потоке луп не найдёт).
    """
    try:
        loop = _main_loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(telemetry_manager.broadcast(event_type, payload, store_id), loop)
    except Exception:
        logger.debug("emit_telemetry_sync: failed to deliver event", exc_info=True)
