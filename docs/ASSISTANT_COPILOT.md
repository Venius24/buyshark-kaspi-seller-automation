# ИИ-помощник продавца: план расширения (Copilot)

Ветка `feat/assistant-copilot`, worktree `F:\Dev\sbx-copilot`. Расширяем `backend/assistant/`, ничего не переписываем с нуля.
Область: магазин Бархан (store_id берётся из `ToolContext`, как и везде). Другие магазины и «старые версии» вне задачи.

## Что уже есть
Цикл `engine.run_turn`, реестр tools с `ToolContext` (store_id/роль/раздел), пишущие tools выключены по умолчанию
(`ASSISTANT_ALLOW_WRITE_TOOLS=0`), `StubProvider` для тестов без сети, хранение диалогов и токенов (`storage.py`),
tools: обзор, заказы, поиск товаров, статус демпинга, продажи, отчёт и RAG. `set_dumping_enabled` остаётся заглушкой.

## Что добавляем (порядок = порядок шагов)
**A. Чтение (без изменений данных) — выполнено**
1. `get_sales_stats` — реальная реализация поверх `state_helpers._load_sales_summary/_load_sales_daily`.
2. `build_report` — отчёт: разделы `sales`, `finance` (комиссии/маржа через `services/order_finance`), `stock`
   (`services/stock_forecast`), `pricing` (радар/демпинг), `problems`. Числа считает код из существующих загрузчиков,
   модель только комментирует. Жёсткие потолки размера ответа.
3. `search_store_knowledge` — RAG по данным магазина: карточки, продажи по дням, снимок ценовой войны, логи демпера,
   остатки. Индекс per-store в памяти с TTL (BM25 на чистом Python, RU+EN), результаты с источником. Интерфейс `Retriever`,
   под который позже подключается эмбеддинг-поиск (`my-rag`: BGE-M3 + pgvector).

**B. Предложения цен (запись только через человека) — выполнено**
4. Таблица `assistant_price_proposals` + alembic-миграция (файл миграции создаётся, на БД НЕ применяется).
5. Tool `propose_price(sku, new_price, reason)`: создаёт `pending`-предложение, не меняет цену. Валидация: цена не ниже
   минимальной цены товара (из конфига демпинга) и не выше максимальной, если задана. Агент вызывает только по запросу пользователя.
6. Эндпоинты `GET /proposals`, `POST /proposals/{id}/approve|reject` (роль не ниже MANAGER). При approve — повторная
   валидация и вызов `PriceGateway`. Реализуем только `DryRunGateway` (журнал, без обращения к Kaspi).
   Реальный шлюз — отдельное решение пользователя, не в этой ветке.

**C. Observability и evals — выполнено**
7. Трассы: на каждый ход — латентность общая и по шагам, токены, стоимость (тарифы из конфига), ошибки tools;
   эндпоинты `GET /runs`, `GET /runs/{id}`, `GET /metrics/summary` (store-scoped).
8. `evals/`: кейсы траекторий вызовов tools (на ScriptedProvider-сценариях) + инварианты: агент не применяет цену сам,
   `propose_price` ниже минимума отклоняется, пустой поиск не ведёт к выдумыванию, чужой store_id недостижим.

## Guardrails (обязательны для любого исполнителя)
- Работать только в `F:\Dev\sbx-copilot`. `F:\Dev\sbx` (основной checkout с чужими правками) не трогать.
- Только локально: без SSH, деплоя, сети, прод-БД, чтения `.env`/ключей/кук. Миграции не применять ни к какой БД, кроме
  временной тестовой SQLite.
- Не коммитить, не пушить, не переключать ветки — коммитит руководитель после проверки.
- Менять только: `backend/assistant/**`, `backend/routers/assistant.py`, новые файлы в `alembic/versions/`,
  `tests/test_assistant_*.py`, `evals/`, `docs/ASSISTANT_COPILOT.md`; в `backend/db/models.py` — только добавить
  новые модели помощника (предложения цен, запуски; существующие модели не менять). Всё остальное (в т.ч. `HANDOFF.md`) — только предложить в отчёте.
- Не дублировать бизнес-логику: считать через существующие `state_helpers`, `order_finance`, `stock_forecast`, `dumper`.
- `store_id`, токен и роль — только из `ToolContext`, никогда из аргументов модели.
- Любой пишущий путь = pending-предложение + одобрение человеком. Агент цену не применяет.
- Тесты без сети; pytest с `--basetemp` вне репозитория: `F:\Dev\sbx\venv\Scripts\python.exe -m pytest tests/test_assistant_*.py -q --basetemp=C:\Users\Wonde\AppData\Local\Temp\sbx_copilot_pytest`.
  Базовая линия до работ: 43 passed (`tests/test_assistant_skeleton.py`) — не сломать.

## Конфигурация и HTTP API

Все параметры — из окружения процесса; `.env`/ключи в рамках работ не читаются.
- `ASSISTANT_ENABLED` (1), `ASSISTANT_PROVIDER` (deepseek; openai/stub также доступны),
  `ASSISTANT_MODEL` (deepseek-chat), `ASSISTANT_API_KEY` (без ключа — stub),
  `ASSISTANT_BASE_URL` (по провайдеру).
- `ASSISTANT_MAX_TOOL_ITERATIONS` (4), `ASSISTANT_HISTORY_LIMIT` (20),
  `ASSISTANT_TIMEOUT_SECONDS` (45), `ASSISTANT_MAX_OUTPUT_TOKENS` (1200).
- `ASSISTANT_ALLOW_WRITE_TOOLS` (0), `ASSISTANT_MAX_PENDING_PROPOSALS` (50),
  `ASSISTANT_PRICE_GATEWAY` (только dry_run), `ASSISTANT_RAG_TTL_SECONDS` (300).
- `ASSISTANT_PRICE_PROMPT_PER_1M`, `ASSISTANT_PRICE_COMPLETION_PER_1M` — USD за
  миллион токенов. Нужны оба конечных неотрицательных тарифа; отсутствующий или
  невалидный тариф означает `cost_usd=null`. Явный 0 разрешён. Стоимость сохраняется
  на момент запуска; сумма учитывает известные стоимости, `priced_runs` показывает
  их количество, при отсутствии известных стоимостей сумма — null.

Префикс всех маршрутов: `/api/v1/assistant`. Требуется раздел `assistant`, магазин
берётся из авторизованного контекста.
- `GET /status`, `GET|POST /conversations`, `DELETE /conversations/{id}`,
  `GET|POST /conversations/{id}/messages` — прежние контракты ответов.
- `GET /proposals`, `POST /proposals/{id}/approve`, `POST /proposals/{id}/reject`
  (решение — MANAGER/OWNER).
- `GET /runs?limit=20` (1–100) — последние запуски без steps;
  `GET /runs/{id}` — подробности со steps, чужой store_id возвращает 404.
- `GET /metrics/summary?days=7` (1–90) — общее число запусков, суммы токенов,
  известной стоимости, доли ошибок и усечения. Ошибочный запуск — `error != null`
  либо хотя бы одна ошибка tool. p50/p95 вычисляются линейной интерполяцией
  `(n-1)*p`, на пустой выборке — null. Перцентили, `tool_errors` и `top_tools`
  используют только последние 5000 запусков периода; `sample` явно сообщает
  лимит, фактический размер, усечение, порядок и затронутые поля. Остальные
  показатели агрегируются SQL по всему периоду.

`AssistantRunDB` и миграция `051_assistant_runs` добавлены; миграция не применялась.
Трасса хранит только kind/name/duration_ms/ok и ошибку до 200 символов; сообщения,
аргументы и результаты tools в steps не записываются. Ошибка запуска ограничена
500 символами. Обычные tool_trace сообщений сохраняют прежний формат. Запись run
выполняется в той же сессии через savepoint: сбой INSERT логируется и не мешает
ответу. При ошибке провайдера сообщения и предложения неуспешного хода откатываются,
как прежде. Затем в той же request-сессии сохраняется только run до возврата
прежнего HTTP 502; отдельный commit нужен из-за rollback в get_db при HTTPException.

## Известные ограничения

- RAG индексирует карточки/остатки, дневные продажи, снимки ценовой войны и логи;
  отдельные заказы в индекс не входят.
- Шлюз цен — только dry-run: одобрение не меняет цену в Kaspi.
- Детерминированные evals проверяют цикл, инструменты и инварианты на заданных
  сценариях, а не качество модели. `python -m evals.run` — 14 кейсов, временная
  SQLite, фикстуры вместо загрузчиков, сеть блокируется, `.env` не читается.
  `--live` реализован для сравнения траекторий с реальным провайдером, в рамках
  этой работы не запускался. Подробности: [evals/README.md](../evals/README.md).
