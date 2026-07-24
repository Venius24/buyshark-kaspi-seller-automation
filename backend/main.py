"""
FastAPI backend — token-first architecture (Модульная сборка).
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.routers import auth, products, orders, dumping, ws, analytics, chatbot, stock_groups, api_keys, print_batcher
from backend.routers.public_api import groups as public_groups

app = FastAPI(title="Kaspi SaaS Wrapper API")

import asyncio
from backend.routers.dumping import background_dumping_loop
from backend.chatbot.worker import kaspi_chatbot_loop, start_telegram_bot_polling
from backend.services.stock_sync_worker import master_stock_radar_loop

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_dumping_loop())
    asyncio.create_task(kaspi_chatbot_loop())
    asyncio.create_task(start_telegram_bot_polling())
    asyncio.create_task(master_stock_radar_loop())

# CORS — разрешаем Vite dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Статика для ping-pong
app.mount("/ping-pong", StaticFiles(directory="api-ping-pong", html=True), name="ping-pong")

# Подключение роутеров
app.include_router(auth.router)
app.include_router(products.router)
app.include_router(orders.router)
app.include_router(dumping.router)
app.include_router(ws.router)
app.include_router(analytics.router)
app.include_router(chatbot.router)
app.include_router(stock_groups.router)
app.include_router(api_keys.router)
app.include_router(print_batcher.router)
app.include_router(public_groups.router)



@app.get("/")
def read_root():
    return {"status": "Kaspi SaaS Wrapper API is running", "auth": "token-first"}
