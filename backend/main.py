"""
FastAPI backend — token-first architecture (Модульная сборка + Auth & Multi-Store Engine).
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import os

# До этой правки корневой логгер оставался на дефолтном WARNING — все
# logger.info(...) по всему приложению (старт бота/воркеров, шедулер
# демпинга и т.д.) молча терялись в проде, journalctl показывал только
# warning/error без "хлебных крошек" об успешном старте. Вызывается ДО
# импорта остальных backend-модулей, чтобы их info-логи тоже не терялись.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator

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
    auth_v1, stores_v1, chats, autolist, auto_assembly, telegram_link,
    pricefeed_import, stores_auto_connect, feature_flags as feature_flags_router,
    dashboard as dashboard_router,
    employees as employees_router,
)
from backend.routers import billing as billing_router, admin_billing, referral as referral_router
from backend.routers import webhooks_incoming_email
from backend.routers import assistant as assistant_router
from backend.routers.public_api import groups as public_groups
from backend.routers.public_api import orders as public_orders

from backend.routers.dumping import background_dumping_loop
from backend.chatbot.worker import kaspi_chatbot_loop, start_telegram_bot_polling
from backend.chatbot.outbound import outbound_loop
from backend.chatbot.notify_dispatch import flush_loop as notification_flush_loop
from backend.services.stock_sync_worker import pickup_campaign_loop, start_packaging_sync_loop
from backend.services.products_sync_worker import start_products_sync_loop
from backend.services.sales_sync_worker import start_sales_sync_loop
from backend.services.kaspi_quality_metrics_worker import start_quality_metrics_loop
from backend.services.billing_worker import start_billing_loop
from backend.workers.health_check_worker import start_health_check_loop

from backend.perf.collector import is_enabled as _perf_enabled

app = FastAPI(title="Kaspi SaaS Wrapper API")

# Uvicorn привязан к 127.0.0.1; nginx отдаёт /metrics только Prometheus
# из Docker-сети. Метрики HTTP агрегируются по шаблону
# маршрута, без ID магазинов и других значений из URL.
Instrumentator(excluded_handlers=[r"^/metrics$"]).instrument(app).expose(
    app, include_in_schema=False
)


# Дефолтный executor лупа — тот самый пул, в который попадает КАЖДЫЙ
# asyncio.to_thread во всём процессе: чтения/записи Мастер-Групп, отметки
# списаний, конфиги чатбота, биллинг. Python выдаёт ему min(32, cpu_count+4)
# потоков, а на этой VPS два ядра — то есть шесть слотов на всё приложение.
#
# Шести хватало, пока никто не приходил залпом. Радар приходил: gather по 316
# товарам, каждый — сетевой поход в kaspi.kz. Замер на проде 2026-09-16: PUT
# /api/stock-groups 54.07с, пришёл через 5с после старта скана и ответил за
# секунду до его конца, при том что сама ручка вне скана отвечает за 0.35с.
# Радару выдан свой пул (backend/dumper/radar.py), а здесь снимается сам
# потолок в шесть слотов — работа тут не считающая, а ожидающая (round-trip в
# Postgres), упирать её в число ядер незачем.
#
# Верхняя граница не произвольная: каждый такой поток берёт соединение из
# синхронного пула в backend/state_helpers.py (10 + 14 overflow = 24). Пул
# должен быть НЕ МЕНЬШЕ числа потоков, иначе очередь просто переезжает на
# checkout соединения.
APP_THREAD_WORKERS = 24


@app.on_event("startup")
async def startup_event():
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=APP_THREAD_WORKERS, thread_name_prefix="app")
    )

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
    asyncio.create_task(outbound_loop())
    asyncio.create_task(start_telegram_bot_polling())
    asyncio.create_task(notification_flush_loop())
    asyncio.create_task(pickup_campaign_loop())
    # Единый писатель цен/остатков в Kaspi: демпер и Мастер-Группы только
    # объявляют желаемое состояние, отправляет их отсюда один флашер с учётом
    # реальной квоты (250 офферов / 30 мин) — см. backend/services/offer_sync.py.
    from backend.services.offer_sync import offer_sync_loop
    asyncio.create_task(offer_sync_loop())
    # Полная сверка каталога прайс-листом. Второго писателя это НЕ добавляет:
    # воркер не решает ничего про отдельные офферы, он объявляет Kaspi всё
    # желаемое состояние целиком. По умолчанию идёт в ТЕНЕВОМ режиме — пишет в
    # лог, что отправил бы, и ничего не отправляет; чтобы файл поехал, нужны
    # XML_FEED_WORKER=1, XML_FEED_UPLOAD_ENABLED=1 и enabled в состоянии
    # магазина. См. backend/dumper/feed_worker.py.
    from backend.dumper.feed_worker import feed_worker_loop
    asyncio.create_task(feed_worker_loop())
    asyncio.create_task(start_packaging_sync_loop(30))
    asyncio.create_task(start_health_check_loop(3600))
    asyncio.create_task(start_products_sync_loop(1800))
    asyncio.create_task(start_sales_sync_loop(1800))
    asyncio.create_task(start_quality_metrics_loop())
    asyncio.create_task(start_billing_loop())
    from backend.services.ops_digest_worker import start_ops_digest_loop
    asyncio.create_task(start_ops_digest_loop())

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
# auto-connect раньше stores_v1: иначе POST /api/v1/stores/auto-connect/verify
# ловит маршрут /api/v1/stores/{store_id}/verify с store_id='auto-connect'.
app.include_router(stores_auto_connect.router)
app.include_router(stores_v1.router)

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(dumping.router)
app.include_router(pricefeed_import.router)
app.include_router(ws.router)
app.include_router(analytics.router)
app.include_router(dashboard_router.router)
app.include_router(chatbot.router)
app.include_router(chats.router)
app.include_router(stock_groups.router)
app.include_router(api_keys.router)
app.include_router(print_batcher.router)
app.include_router(public_groups.router)
app.include_router(public_orders.router)
app.include_router(autolist.router)
app.include_router(auto_assembly.router)
app.include_router(telegram_link.router)
app.include_router(billing_router.router)
app.include_router(admin_billing.router)
app.include_router(referral_router.router)
app.include_router(webhooks_incoming_email.router)
app.include_router(feature_flags_router.router)
app.include_router(employees_router.router)
app.include_router(assistant_router.router)

if _perf_enabled():
    from backend.perf.router import router as perf_router
    app.include_router(perf_router)


@app.get("/")
def read_root():
    return {"status": "Kaspi SaaS Wrapper API is running", "auth": "token-first", "v1": "active"}
