# ИИ-помощник продавца: план расширения (Copilot)

Ветка `feat/assistant-copilot`, worktree `F:\Dev\sbx-copilot`. Расширяем `backend/assistant/`, ничего не переписываем с нуля.
Область: магазин Бархан (store_id берётся из `ToolContext`, как и везде). Другие магазины и «старые версии» вне задачи.

## Что уже есть
Цикл `engine.run_turn`, реестр tools с `ToolContext` (store_id/роль/раздел), пишущие tools выключены по умолчанию
(`ASSISTANT_ALLOW_WRITE_TOOLS=0`), `StubProvider` для тестов без сети, хранение диалогов и токенов (`storage.py`),
tools: обзор, заказы, поиск товаров, статус демпинга; `get_sales_stats` и `set_dumping_enabled` — заглушки.

## Что добавляем (порядок = порядок шагов)
**A. Чтение (без изменений данных)**
1. `get_sales_stats` — реальная реализация поверх `state_helpers._load_sales_summary/_load_sales_daily`.
2. `build_report` — отчёт: разделы `sales`, `finance` (комиссии/маржа через `services/order_finance`), `stock`
   (`services/stock_forecast`), `pricing` (радар/демпинг), `problems`. Числа считает код из существующих загрузчиков,
   модель только комментирует. Жёсткие потолки размера ответа.
3. `search_store_knowledge` — RAG по данным магазина: карточки, продажи по дням, снимок ценовой войны, логи демпера,
   остатки. Индекс per-store в памяти с TTL (BM25 на чистом Python, RU+EN), результаты с источником. Интерфейс `Retriever`,
   под который позже подключается эмбеддинг-поиск (`my-rag`: BGE-M3 + pgvector).

**B. Предложения цен (запись только через человека)**
4. Таблица `assistant_price_proposals` + alembic-миграция (файл миграции создаётся, на БД НЕ применяется).
5. Tool `propose_price(sku, new_price, reason)`: создаёт `pending`-предложение, не меняет цену. Валидация: цена не ниже
   минимальной цены товара (из конфига демпинга) и не выше максимальной, если задана. Агент вызывает только по запросу пользователя.
6. Эндпоинты `GET /proposals`, `POST /proposals/{id}/approve|reject` (роль не ниже MANAGER). При approve — повторная
   валидация и вызов `PriceGateway`. Реализуем только `DryRunGateway` (журнал, без обращения к Kaspi).
   Реальный шлюз — отдельное решение пользователя, не в этой ветке.

**C. Observability и evals**
7. Трассы: на каждый ход — латентность общая и по шагам, токены, стоимость (тарифы из конфига), ошибки tools;
   эндпоинты `GET /runs`, `GET /runs/{id}`, `GET /metrics/summary` (store-scoped).
8. `evals/`: кейсы траекторий вызовов tools (на StubProvider-сценариях) + инварианты: агент не применяет цену сам,
   `propose_price` ниже минимума отклоняется, пустой поиск не ведёт к выдумыванию, чужой store_id недостижим.

## Guardrails (обязательны для любого исполнителя)
- Работать только в `F:\Dev\sbx-copilot`. `F:\Dev\sbx` (основной checkout с чужими правками) не трогать.
- Только локально: без SSH, деплоя, сети, прод-БД, чтения `.env`/ключей/кук. Миграции не применять ни к какой БД, кроме
  временной тестовой SQLite.
- Не коммитить, не пушить, не переключать ветки — коммитит руководитель после проверки.
- Менять только: `backend/assistant/**`, `backend/routers/assistant.py`, новые файлы в `alembic/versions/`,
  `tests/test_assistant_*.py`, `evals/`, `docs/ASSISTANT_COPILOT.md`; в `backend/db/models.py` — только добавить
  новую модель предложений цен (существующие модели не менять). Всё остальное (в т.ч. `HANDOFF.md`) — только предложить в отчёте.
- Не дублировать бизнес-логику: считать через существующие `state_helpers`, `order_finance`, `stock_forecast`, `dumper`.
- `store_id`, токен и роль — только из `ToolContext`, никогда из аргументов модели.
- Любой пишущий путь = pending-предложение + одобрение человеком. Агент цену не применяет.
- Тесты без сети; pytest с `--basetemp` вне репозитория: `F:\Dev\sbx\venv\Scripts\python.exe -m pytest tests/test_assistant_*.py -q --basetemp=C:\Users\Wonde\AppData\Local\Temp\sbx_copilot_pytest`.
  Базовая линия до работ: 43 passed (`tests/test_assistant_skeleton.py`) — не сломать.
