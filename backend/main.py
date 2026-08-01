"""
FastAPI backend — token-first architecture (Модульная сборка + Auth & Multi-Store Engine).
"""

import asyncio
import os
import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Локальный .env не подхватывается системой автоматически (на VPS это делает
# systemd EnvironmentFile) — читаем его вручную, как в backend/chatbot/config.py
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
if os.path.exists(_env_path):
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

SENTRY_DSN = os.getenv("SENTRY_DSN_BACKEND")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
    )

from backend.db.database import init_db
from backend.db.seed import seed_default_admin
from backend.routers import (
    auth, products, orders, dumping, ws, analytics,
    chatbot, stock_groups, api_keys, print_batcher,
    auth_v1, stores_v1, chats, autolist
)
from backend.routers.public_api import groups as public_groups

from backend.routers.dumping import background_dumping_loop
from backend.chatbot.worker import kaspi_chatbot_loop, start_telegram_bot_polling
from backend.services.stock_sync_worker import master_stock_radar_loop, start_packaging_sync_loop
from backend.services.products_sync_worker import start_products_sync_loop
from backend.workers.health_check_worker import start_health_check_loop

from backend.perf.collector import is_enabled as _perf_enabled

app = FastAPI(title="Kaspi SaaS Wrapper API")


@app.on_event("startup")
async def startup_event():
    # Инициализация схемы БД и админ-аккаунта
    await init_db()
    await seed_default_admin()

    # Ссылка на главный event loop нужна emit_telemetry_sync() для доставки
    # WS-событий, когда её вызывают из рабочего потока (asyncio.to_thread) —
    # там asyncio.get_event_loop() луп не найдёт.
    from backend.dumper import telemetry
    telemetry.set_main_loop(asyncio.get_running_loop())

    # Фоновые процессы
    asyncio.create_task(background_dumping_loop())
    asyncio.create_task(kaspi_chatbot_loop())
    asyncio.create_task(start_telegram_bot_polling())
    asyncio.create_task(master_stock_radar_loop())
    asyncio.create_task(start_packaging_sync_loop(30))
    asyncio.create_task(start_health_check_loop(3600))
    asyncio.create_task(start_products_sync_loop(1800))

    # Опциональная инструментация производительности (PERF_PROFILING=1) —
    # см. backend/perf/. Ноль накладных расходов, если флаг не выставлен.
    if _perf_enabled():
        from backend.perf.http_hooks import install_http_hooks
        from backend.perf.loop_monitor import monitor_event_loop_lag
        install_http_hooks()
        asyncio.create_task(monitor_event_loop_lag())

# CORS — разрешаем Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if _perf_enabled():
    from backend.perf.middleware import PerfTimingMiddleware
    app.add_middleware(PerfTimingMiddleware)

# Статика для ping-pong
app.mount("/ping-pong", StaticFiles(directory="api-ping-pong", html=True), name="ping-pong")

# Подключение роутеров v1 и стандартных
app.include_router(auth_v1.router)
app.include_router(stores_v1.router)

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(dumping.router)
app.include_router(ws.router)
app.include_router(analytics.router)
app.include_router(chatbot.router)
app.include_router(chats.router)
app.include_router(stock_groups.router)
app.include_router(api_keys.router)
app.include_router(print_batcher.router)
app.include_router(public_groups.router)
app.include_router(autolist.router)

if _perf_enabled():
    from backend.perf.router import router as perf_router
    app.include_router(perf_router)


@app.get("/")
def read_root():
    return {"status": "Kaspi SaaS Wrapper API is running", "auth": "token-first", "v1": "active"}
