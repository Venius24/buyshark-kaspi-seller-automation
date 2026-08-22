import asyncio
import os
import re
import logging
import datetime
import time
from typing import Optional, List, Dict

from backend.state_helpers import (
    _load_master_stock_groups,
    _save_master_stock_groups,
    _load_processed_orders,
    _save_processed_orders,
    _load_overrides,
    _save_override,
    _load_products,
    _save_products,
)
from backend.kaspi_wrapper import KaspiAPI
from backend.services import offer_sync
from backend.dumper.config import discover_merchant_id, _decode_base64_if_needed
from backend.dumper.telemetry import telemetry_manager
from backend.services.webhook_dispatcher import dispatch_webhook_event
from backend.security.store_context import list_active_stores
from backend.chatbot.pickup_campaign import maybe_trigger_pickup_campaign


logger = logging.getLogger(__name__)

# Per-store locks for thread/async-safe group stock operations (Phase 7) —
# store A's sync no longer blocks on store B's lock. store_id=None keeps the
# legacy single-tenant key, so pre-migration callers behave exactly as before
# (one shared lock, same as when this was a single global asyncio.Lock()).
_stock_locks: dict = {}

def get_stock_lock(store_id=None) -> asyncio.Lock:
    if store_id not in _stock_locks:
        _stock_locks[store_id] = asyncio.Lock()
    return _stock_locks[store_id]

# ── Окно выборки авто-списания ─────────────────────────────────────────
# Раньше каждый 30-секундный тик качал ПОЛНЫЙ 13-дневный обход /orders
# (все страницы, все три вкладки) — ~66 тыс. запросов в сутки, чтобы найти
# заказы, появившиеся за последние 30 секунд (HANDOFF §3.54).
#
# Схема теперь двухуровневая, с гарантией непотери списаний:
#   • Горячий путь — узкое окно DEDUCTION_HOT_WINDOW_DAYS: обычное
#     появление заказа списывается за один тик, как и раньше.
#   • Полный 13-дневный свип — при старте процесса (ловит всё, что
#     накопилось, пока сервис лежал) и раз в
#     DEDUCTION_FULL_SWEEP_INTERVAL_SECONDS (ловит заказы старше горячего
#     окна, которые по любой причине ещё не списаны). Свип стоит ~24
#     обхода в сутки вместо 2880.
# Отметка свипа сдвигается только после УСПЕШНОЙ выборки: упавший обход
# ретраится следующим тиком. Права на списание персистентны
# (processed_orders), поэтому повторная видимость заказа не создаёт
# двойных списаний.
DEDUCTION_HOT_WINDOW_DAYS = max(1, int(os.getenv("DEDUCTION_HOT_WINDOW_DAYS", "3")))
FULL_SWEEP_WINDOW_DAYS = 13
DEDUCTION_FULL_SWEEP_INTERVAL_SECONDS = float(
    os.getenv("DEDUCTION_FULL_SWEEP_INTERVAL_SECONDS", "3600")
)
_last_full_sweep_at: Dict[Optional[str], float] = {}

async def notify_zero_stock_telegram(group_name: str, group_id: str, linked_skus: List[str], store_id: Optional[str] = None, trigger_order_info: Optional[dict] = None, shared_stock: int = 0, stop_qty: int = 5):
    """Уведомление при просаде остатка группы ниже (или равно) её "Стоп
    кол-ва" (по умолчанию 5, настраивается per-группа) с деталями о
    заказе-триггере — реальным привязанным через deep-link пользователям
    магазина (группа 'problems'), через notify_dispatch. Раньше слало
    напрямую по легаси admin_ids (см. HANDOFF, вырезано 2026-08-09)."""
    try:
        from backend.chatbot import notify_dispatch
        from backend.chatbot.notifications import _esc_md

        if store_id is None:
            logger.info("Stop Qty notify skipped: без store_id некому отправлять (легаси-путь без магазина).")
            return

        skus_str = ", ".join(linked_skus[:5]) + ("..." if len(linked_skus) > 5 else "")

        order_section = ""
        if trigger_order_info:
            code = trigger_order_info.get("order_code") or trigger_order_info.get("code") or "N/A"
            sku = trigger_order_info.get("sku") or "N/A"
            units = trigger_order_info.get("units") or 1
            title = trigger_order_info.get("title") or "Товар"
            customer = trigger_order_info.get("customer") or "Покупатель"

            order_section = (
                f"🛒 *ПРИЧИНА ПРОСАДА (Новый заказ):*\n"
                f"• *№ Заказа:* `#{_esc_md(code)}`\n"
                f"• *Товар:* {_esc_md(title)}\n"
                f"• *Артикул списания:* `{_esc_md(sku)}` (-{units} шт.)\n"
                f"• *Покупатель:* {_esc_md(customer)}\n\n"
            )

        text = (
            f"🚨 *ВНИМАНИЕ: СНЯТО С ПРОДАЖИ (Стоп кол-во достигнуто)*\n\n"
            f"📦 *Группа:* {_esc_md(group_name)}\n"
            f"🆔 *ID Группы:* `{group_id}`\n"
            f"📉 *Доступный остаток:* {shared_stock} шт. (стоп кол-во: {stop_qty} шт.)\n"
            f"🔗 *Привязанные SKU:* {_esc_md(skus_str)}\n\n"
            f"{order_section}"
            f"⚠️ Все карточки товаров группы временно сняты с продажи на Kaspi!"
        )

        await notify_dispatch.dispatch(store_id, "problems", text, urgent=True, dedup_key=f"stop_qty:{group_id}", card="stock_stopped")
    except Exception as e:
        logger.error(f"Failed to execute notify_zero_stock_telegram: {e}")

async def notify_oversold_telegram(
    group_name: str, group_id: str, store_id: Optional[str],
    order_code: str, sku: str, units: int, old_stock: int, debt: int,
) -> None:
    """Заказов пришло больше, чем числилось остатка.

    Отдельно от notify_zero_stock_telegram: то — «остаток подошёл к стопу,
    сняли с продажи», штатная ситуация. А здесь товар УЖЕ продан и его нечем
    закрыть — владельцу надо узнать сразу, а не из журнала группы.
    """
    if store_id is None:
        return
    try:
        from backend.chatbot import notify_dispatch
        from backend.chatbot.notifications import _esc_md

        shortfall = max(0, units - old_stock)
        text = (
            f"🚨 *ПЕРЕПРОДАЖА: заказов больше, чем остатка*\n\n"
            f"📦 *Группа:* {_esc_md(group_name)}\n"
            f"🛒 *Заказ:* `#{_esc_md(str(order_code))}` — списал {units} шт. "
            f"по артикулу `{_esc_md(str(sku))}`\n"
            f"📉 *Было на остатке:* {old_stock} шт., не хватает *{shortfall} шт.*\n"
            + (f"🧾 *Всего долг по группе:* {debt} шт.\n" if debt != shortfall else "")
            + f"\n⚠️ Позиции группы сняты с продажи. Заказ уже принят — его нужно "
              f"закрыть физически или отменить в кабинете."
        )
        await notify_dispatch.dispatch(
            store_id, "problems", text, urgent=True,
            dedup_key=f"oversold:{group_id}:{order_code}", card="oversold",
        )
    except Exception as e:
        logger.error(f"Failed to execute notify_oversold_telegram: {e}")


async def broadcast_ws_event(event_type: str, data: dict, store_id: Optional[str] = None):
    """Вещание WebSocket события на клиентские UI-подключения того же стора."""
    try:
        await telemetry_manager.broadcast(event_type, data, store_id=store_id)
    except Exception as e:
        logger.error(f"Error broadcasting WS event {event_type}: {e}")


# Сколько ждём завершения рассылки, прежде чем счесть её оборванной. Реальная
# рассылка укладывается в секунды, так что запас тут десятикратный.
STUCK_STATUS_AFTER_SECONDS = 600


async def _release_stuck_broadcast_status(group_id: str, store_id: Optional[str], reason: str):
    """Снять с группы индикатор «идёт списание», когда рассылка не дошла до конца.

    Само списание при этом уже состоялось и остаток уменьшен — теряется только
    рассылка нового остатка по SKU, поэтому статус «предупреждение», а не
    «синхронизировано»: владелец должен увидеть, что до Kaspi дошло не всё.
    """
    try:
        async with get_stock_lock(store_id):
            groups = _load_master_stock_groups(store_id=store_id)
            g = groups.get(group_id)
            if not g or g.get("status") not in ("deducting", "updating"):
                return
            g["status"] = "warning"
            g.pop("status_since", None)
            msg = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ⚠️ Рассылка остатка не завершилась ({reason})"
            g["last_sync_log"] = msg
            g["sync_logs"] = ([msg] + list(g.get("sync_logs") or []))[:30]
            _save_master_stock_groups(groups, store_id=store_id)
        await broadcast_ws_event("stock_group_updated", g, store_id=store_id)
    except Exception:
        logger.exception("[Broadcast Sync] Не удалось снять залипший статус группы %s", group_id)


async def execute_broadcast_sync(group_id: str, token: str, store_id: Optional[str] = None, trigger_order_info: Optional[dict] = None):
    """
    Точка входа в веерную рассылку. Тонкая обёртка, и вот зачем она нужна.

    Рассылку ВСЕГДА запускают в фоне — `asyncio.create_task` в упаковщике и
    `BackgroundTasks` в роутерах. У фоновой задачи исключение никто не читает,
    оно исчезает молча. А статус «идёт списание» ставится в самом начале, до
    похода в Kaspi: упади рассылка после этого — карточка группы навсегда
    осталась бы со спиннером, который уже некому снять.
    """
    try:
        await _execute_broadcast_sync_impl(
            group_id, token, store_id=store_id, trigger_order_info=trigger_order_info
        )
    except asyncio.CancelledError:
        await _release_stuck_broadcast_status(group_id, store_id, "остановлена")
        raise
    except Exception as e:
        logger.exception(
            "❌ [Broadcast Sync] Группа %s: рассылка упала (%s) — снимаю индикатор списания.",
            group_id, e,
        )
        await _release_stuck_broadcast_status(group_id, store_id, f"{type(e).__name__}: {e}")


async def _execute_broadcast_sync_impl(group_id: str, token: str, store_id: Optional[str] = None, trigger_order_info: Optional[dict] = None):
    """
    Веерная рассылка обновлённого остатка по всем SKU группы.

    Больше не ходит в Kaspi сама: объявляет желаемый остаток в
    backend/services/offer_sync.py, а отправкой занимается единый флашер. Это
    даёт схлопывание — пять заказов подряд по одной группе оставляют по
    каждому SKU ОДНО намерение с финальным остатком, а не пять записей.

    Поверх этого работает лестница шагов (решение пользователя): чем больше
    остаток, тем крупнее должна быть накопленная разница, чтобы её вообще
    стоило слать. На проде было видно, как группа из 14 SKU трижды подряд
    рассылала остатки 1006 / 1006 / 998 — 42 записи квоты, из которых ни одна
    ничего осмысленного не меняла. С лестницей их будет ноль.

    store_id=None (легаси) — нескоуп-чтение/запись группы, как было всегда.
    store_id задан (Phase 7) — группа читается/сохраняется в рамках конкретного стора.
    """
    async with get_stock_lock(store_id):
        groups = _load_master_stock_groups(store_id=store_id)
        group = groups.get(group_id)
        if not group or not group.get("is_active", True):
            return

        # Когда рассылку запустил заказ, на карточке группы должно быть видно
        # именно списание, а не абстрактная «синхронизация» — иначе владелец не
        # отличает продажу от ручной правки остатка.
        if trigger_order_info:
            group["status"] = "deducting"
            group["last_sync_log"] = (
                f"Списание по заказу #{trigger_order_info.get('order_code')} "
                f"({trigger_order_info.get('sku')}): рассылка остатка по SKU группы..."
            )
        else:
            group["status"] = "updating"
            group["last_sync_log"] = "Запуск веерной синхронизации..."
        # Метка времени, по которой сторож отличает идущую рассылку от
        # оборванной (например, сервис перезапустили в середине).
        group["status_since"] = datetime.datetime.now().isoformat()
        _save_master_stock_groups(groups, store_id=store_id)

    await broadcast_ws_event("stock_group_updated", group, store_id=store_id)

    linked_skus = group.get("linked_skus", [])
    shared_stock = int(group.get("shared_stock", 0))
    stop_qty = int(group.get("stop_qty", 5))
    group_name = group.get("group_name", "Без названия")

    logger.info(f"🔄 [Broadcast Sync] Начало обхода {len(linked_skus)} SKU для группы '{group_name}' (Остаток: {shared_stock})")

    # Загружаем товары из кэша (per-store, см. state_helpers._load_products)
    all_products = _load_products(store_id)

    # Куки здесь больше не проверяем: намерения ставятся независимо от
    # состояния сессии, а отправкой (и автообновлением сессии при 401)
    # занимается флашер offer_sync.
    merchant_id = discover_merchant_id(token)
    if not merchant_id:
        # НЕ подставляем чужой merchant_id (раньше был хардкод "20186986" —
        # реальный ID Barkhan: магазин без резолвящегося ID тихо слал бы
        # обновления остатка под его личностью и ловил 403 от Kaspi на
        # каждом SKU). Локальные оверрайды (см. _save_override ниже) всё
        # равно сохранятся — не дойдёт только реальный push в Kaspi MC.
        logger.warning(f"🔄 [Broadcast Sync] Не удалось определить merchant_id для группы '{group_name}' — обновления остатка сохранятся локально, но не уйдут в Kaspi MC.")

    success_count = 0
    fail_count = 0
    skipped_count = 0
    logs = []

    processed_physical_skus = set()
    for idx, sku in enumerate(linked_skus):
        t_now = datetime.datetime.now().strftime("%H:%M:%S")
        # Находим товар в ассортименте
        target_p = next((p for p in all_products if p.get("sku") == sku or p.get("merchantSku") == sku or p.get("kaspiSku") == sku), None)
        if not target_p:
            target_p = {"sku": sku, "merchantSku": sku, "name": f"Товар {sku}", "price_num": 1000}

        physical_key = target_p.get("merchantSku") or target_p.get("sku") or sku
        if physical_key in processed_physical_skus:
            continue
        processed_physical_skus.add(physical_key)

        raw_price_num = target_p.get("price_num")
        if raw_price_num is not None and isinstance(raw_price_num, (int, float)) and raw_price_num > 0:
            price = int(raw_price_num)
        else:
            raw_str = str(target_p.get("price") or "1000").replace("₸", "").replace(" ", "").strip()
            try:
                price = int(raw_str)
            except ValueError:
                price = 1000
        master_sku = target_p.get("kaspiSku") or target_p.get("sku") or sku
        decoded_master = _decode_base64_if_needed(master_sku)

        store_full_id = target_p.get("storeId") or f"{merchant_id}_001"
        store_suffix = store_full_id.split("_")[1] if "_" in store_full_id else "001"
        status_text = "Активен" if shared_stock > stop_qty else "Архив"
        # При просаде до/ниже "Стоп кол-ва" на Kaspi уходит именно 0 —
        # иначе карточка так и останется доступной для покупки с реальным
        # (ещё ненулевым) остатком, хотя мы считаем её "снятой с продажи".
        kaspi_push_stock = shared_stock if shared_stock > stop_qty else 0

        # Сохраняем локальный оверрайд
        _save_override(sku, price, shared_stock, target_p.get("brand"), status_text, store_id=store_id)

        # Обновляем в кэше товаров
        for p in all_products:
            if p.get("sku") == sku or p.get("merchantSku") == sku or p.get("kaspiSku") == sku:
                p["stock"] = shared_stock
                p["status"] = status_text
                break

        if store_id is None:
            # Легаси-путь без стора: очередь намерений скоупится по store_id,
            # ставить её некуда — ограничиваемся локальным оверрайдом выше.
            continue

        physical_sku = target_p.get("merchantSku") or sku

        # Лестница шагов: сравниваем с тем, что Kaspi уже знает (ещё не
        # отправленное намерение, иначе последняя отправка, иначе факт).
        last_pushed = await asyncio.to_thread(offer_sync.last_known_stock, store_id, physical_sku)
        if not offer_sync.should_push_stock(last_pushed, kaspi_push_stock, stop_qty=stop_qty):
            skipped_count += 1
            msg = (
                f"[{t_now}] ⏭ Группа '{group_name}' -> SKU {sku}: остаток "
                f"{last_pushed} -> {kaspi_push_stock}, разница меньше шага "
                f"{offer_sync.stock_step(min(last_pushed, kaspi_push_stock))} — запись отложена (экономия квоты)"
            )
            logs.append(msg)
            await broadcast_ws_event("log", msg, store_id=store_id)
            continue

        await asyncio.to_thread(
            offer_sync.enqueue,
            store_id,
            physical_sku,
            priority=(offer_sync.Priority.WITHDRAW if kaspi_push_stock <= 0 else offer_sync.Priority.MINOR),
            reason=f"group:{group_id}",
            stock=kaspi_push_stock,
            available=kaspi_push_stock > 0,
            master_sku=decoded_master,
            model=target_p.get("name", "Товар"),
            store_suffix=store_suffix,
            city_id="750000000",
        )
        success_count += 1
        msg = f"[{t_now}] ✓ Группа '{group_name}' -> SKU {sku}: остаток {kaspi_push_stock} поставлен в очередь на отправку"
        logs.append(msg)
        await broadcast_ws_event("log", msg, store_id=store_id)

    # Сохраняем обновлённый кэш товаров
    if all_products:
        try:
            _save_products(all_products, store_id)
        except Exception:
            logger.exception("Failed to persist updated products cache for store %s", store_id)

    # Итоговое обновление статуса группы
    async with get_stock_lock(store_id):
        groups = _load_master_stock_groups(store_id=store_id)
        if group_id in groups:
            g = groups[group_id]
            g["last_synced_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            order_tail = (
                f" Причина: заказ #{trigger_order_info.get('order_code')}."
                if trigger_order_info else ""
            )
            # Знаменатель — число РЕАЛЬНЫХ товаров (после дедупа по physical_key
            # выше), а не сырых identifiers linked_skus (merchantSku+kaspiSku на
            # каждый товар) — иначе счётчик всегда выглядел вдвое "недоделанным".
            g["last_sync_log"] = (
                f"В очередь: {success_count}/{len(processed_physical_skus)}. "
                f"Пропущено по шагу: {skipped_count}. Ошибок: {fail_count}.{order_tail}"
            )
            if fail_count == 0:
                g["status"] = "synced"
            elif success_count > 0:
                g["status"] = "warning"
            else:
                g["status"] = "error"
            # Рассылка дошла до конца — сторожу больше нечего сторожить.
            g.pop("status_since", None)

            # Строку о списании (её пишет упаковщик ДО запуска рассылки) не
            # затираем: раньше здесь стояло присваивание, и журнал группы терял
            # ровно то событие, ради которого рассылка и запускалась.
            g["sync_logs"] = (logs + list(g.get("sync_logs") or []))[:30]
            _save_master_stock_groups(groups, store_id=store_id)
            await broadcast_ws_event("stock_group_updated", g, store_id=store_id)

    # Обработка Stop Qty Protection: остаток просел до/ниже кастомного "Стоп кол-ва" группы
    if shared_stock <= stop_qty:
        logger.warning(f"🚨 [Stop Qty Protection] Группа '{group_name}' остаток {shared_stock} <= стоп кол-во {stop_qty}!")
        await notify_zero_stock_telegram(group_name, group_id, linked_skus, store_id=store_id, trigger_order_info=trigger_order_info, shared_stock=shared_stock, stop_qty=stop_qty)
        await broadcast_ws_event("zero_stock_alert", {
            "group_id": group_id,
            "group_name": group_name,
            "message": f"Группа [{group_name}] снята с продажи на Kaspi (остаток {shared_stock} <= стоп кол-во {stop_qty})!"
        }, store_id=store_id)

def _sweep_stuck_statuses(active_groups: dict) -> bool:
    """Снять индикатор списания с групп, чья рассылка оборвалась вместе с процессом.

    Обёртка `execute_broadcast_sync` ловит исключения сама, но от `kill -9` и
    рестарта сервиса она не спасает: группа остаётся в состоянии «идёт
    списание» навсегда, и на карточке крутится спиннер, которого уже некому
    снять. Проверка стоит на горячем тике упаковки — она чисто локальная, ни
    одного запроса наружу.

    Возвращает True, если что-то поправили (значит, группы надо сохранить).
    """
    now = datetime.datetime.now()
    touched = False
    for g in active_groups.values():
        if g.get("status") not in ("deducting", "updating"):
            continue
        since_raw = g.get("status_since")
        if not since_raw:
            # Метки нет — это группа, зависшая ещё до появления сторожа.
            # Ставим метку сейчас, снимем на следующем тике после таймаута.
            g["status_since"] = now.isoformat()
            touched = True
            continue
        try:
            since = datetime.datetime.fromisoformat(str(since_raw))
        except ValueError:
            g["status_since"] = now.isoformat()
            touched = True
            continue
        if (now - since).total_seconds() < STUCK_STATUS_AFTER_SECONDS:
            continue

        g["status"] = "warning"
        g.pop("status_since", None)
        msg = (
            f"[{now.strftime('%H:%M:%S')}] ⚠️ Рассылка остатка оборвалась "
            f"(статус висел больше {STUCK_STATUS_AFTER_SECONDS // 60} мин) — индикатор снят"
        )
        g["last_sync_log"] = msg
        g["sync_logs"] = ([msg] + list(g.get("sync_logs") or []))[:30]
        touched = True
        logger.warning(
            "🧹 [StockSweep] Группа '%s' висела в состоянии списания — статус снят.",
            g.get("group_name"),
        )
    return touched


async def _run_pickup_campaign_pass(token: str, store_id: Optional[str] = None):
    """
    Один проход кампании «Самовывоз» для одного магазина.

    Раньше это был «радар NEW-заказов»: опрашивал NEW/APPROVED_BY_BANK каждые
    20 секунд, списывал остаток по совпавшим Мастер-Группам и попутно дёргал
    кампанию. Аудит 2026-08-13 (HANDOFF 3.46) показал, что он не поймал НИ
    ОДНОГО заказа с 11 августа: в стадии NEW заказы не задерживаются, и все 42
    списания за сутки сделал упаковщик. То есть ~8600 запросов в сутки уходило
    впустую, а кампания самовывоза, повешенная на этот же перебор, физически не
    могла сработать — вдобавок она искала тип «Самовывоз», которого не
    существовало из-за неверного разбора вида доставки.

    Теперь: списание остатка целиком на упаковщике (он смотрит обе вкладки —
    «На сборке» и «Передача»), а здесь остался только триггер кампании, и
    опрашиваем мы ту стадию, где самовывозные заказы действительно лежат
    (PICKUP), а не NEW.
    """
    if not store_id:
        return

    # Нет настроенных точек самовывоза — звать клиента некуда, и тогда мы не
    # делаем к Kaspi ни одного запроса.
    from backend.chatbot.prompt import get_profile as _get_bot_profile
    profile = await asyncio.to_thread(_get_bot_profile, store_id)
    if not profile.get("pickup_points"):
        return

    try:
        orders_resp = await asyncio.to_thread(
            KaspiAPI.get_orders_with_products,
            token,
            state="PICKUP",
            status="APPROVED_BY_BANK",
            limit=50,
            assembled=None,
        )
    except Exception as req_ex:
        # Раньше это был logger.debug — сбой канала к Kaspi был не виден вообще.
        logger.warning(f"[PickupCampaign] Не удалось получить заказы самовывоза: {req_ex}")
        return

    if not orders_resp:
        return

    processed_orders = await asyncio.to_thread(_load_processed_orders, store_id)
    changed = False

    for order in orders_resp:
        order_id = str(order.get("id") or order.get("internalId") or "")
        if not order_id or order_id in processed_orders:
            continue

        # В стадии PICKUP лежат И постаматные заказы (их там большинство:
        # 51 из 60 на замере 13.08). Клиент забирает такой заказ из ячейки
        # Kaspi, а не приезжает к продавцу — писать ему адрес магазина нельзя.
        if order.get("deliveryType") != "Самовывоз":
            continue

        asyncio.create_task(maybe_trigger_pickup_campaign(store_id, token, order))
        processed_orders[order_id] = {
            "processed_at": datetime.datetime.now().isoformat(),
            "matched_groups": [],
        }
        changed = True

    if changed:
        await asyncio.to_thread(_save_processed_orders, processed_orders, store_id)

async def run_pickup_campaign_cycle():
    """
    Один цикл кампании «Самовывоз» по всем активным сторам. Вынесено из
    pickup_campaign_loop отдельной функцией для юнит-тестов (без бесконечного
    цикла/sleep вокруг).
    """
    stores = await list_active_stores()
    for store in stores:
        try:
            await _run_pickup_campaign_pass(store.token, store.store_id)
        except Exception as e:
            logger.error(f"[PickupCampaign] Store {store.store_id} pass failed: {e}")

async def pickup_campaign_loop():
    """
    Фоновый триггер кампании «Самовывоз» по всем активным сторам.

    Интервал 60с, а не прежние 20: это приветственное сообщение клиенту с
    адресом и графиком, а не движок остатков — секунды здесь ничего не решают,
    зато запросов к Kaspi втрое меньше.
    """
    logger.info("🟢 [PickupCampaign] Запущен фоновый триггер кампании «Самовывоз».")
    while True:
        try:
            await asyncio.sleep(60)
            await run_pickup_campaign_cycle()

        except asyncio.CancelledError:
            logger.info("⏹ [PickupCampaign] Триггер остановлен.")
            break
        except Exception as e:
            logger.error(f"❌ [PickupCampaign] Ошибка в цикле: {e}")


# ── 5. Auto Deduction on Packaging Detection Engine ──────────────────────

# ── Сопоставление артикула заказа с Мастер-Группой ───────────────────────
#
# Правило было широким: подстрока в обе стороны ПЛЮС поиск ключа группы в
# НАЗВАНИИ товара. Замер 13.08 по всем ~1500 заказам магазина (HANDOFF 3.46):
# строгое правило даёт те же 181 совпадение, широкое добавляет ровно один
# артикул — и тот лишь из-за приписки от руки в merchantSku ("H422-0007-9
# 2шт"). Обратных случаев ноль. То есть широта не приносила ничего, кроме
# риска списать у чужой группы. Приписку добиваем нормализацией.
#
# Оговорка от 14.08: приписка через пробел НЕ всегда мусор. У Бархана
# "H422-0007-9" и "H422-0007-9 2шт" — два разных товара, лежащих в разных
# группах ("H422-0007 1шт" и "H422-0007 2 шт"). Нормализация схлопывала их в
# один ключ, обе группы отвечали на артикул как на свой, и списание молча
# пропускалось на каждом проходе (652 предупреждения за ночь). Поэтому
# сначала сверяемся с артикулом КАК ОН ЕСТЬ и только потом нормализуем.

_MULTIPACK_RE = re.compile(r"^(.+?)-(\d{1,2})$")


def _canon_sku(raw) -> str:
    """Артикул как он записан: снят регистр и лишние пробелы, приписка цела.

    Это точный ключ. "H422-0007-9 2шт" им остаётся и не путается с
    "H422-0007-9" — в отличие от _normalize_sku.
    """
    return " ".join(str(raw or "").split()).lower()


def _normalize_sku(raw) -> str:
    """Артикул в сравнимом виде: без регистра и без человеческих приписок.

    В merchantSku регулярно попадает хвост через пробел — "H422-0007-9 2шт",
    "X474-0018-6 1шт". Код товара — это первый токен.
    """
    s = str(raw or "").strip().lower()
    return s.split()[0] if s else ""


def _sku_base(sku: str) -> str:
    """База мультипака: "t219-0005-4" -> "t219-0005". Не мультипак — сам себя."""
    m = _MULTIPACK_RE.match(sku)
    return m.group(1) if m else sku


def match_group_for_sku(m_sku: str, groups: dict):
    """
    Мастер-группа для артикула из заказа. Возвращает (group_id, group) либо
    (None, None).

    Правила перебираются от самого строгого к самому вольному, и первое же
    давшее совпадения — последнее:

    1. артикул КАК ОН ЕСТЬ, вместе с припиской ("H422-0007-9 2шт");
    2. артикул без приписки ("H422-0007-9 2шт" -> "h422-0007-9");
    3. база мультипака ("T219-0005-4" при привязанном "T219-0005").

    Порядок важен дважды. Пункт 1 перед 2 — иначе "H422-0007-9" и
    "H422-0007-9 2шт" из разных групп схлопываются в один ключ и обе группы
    считают артикул своим. Пункт 2 перед 3 — иначе "K474-0003-1" и
    "K474-0003-2", лежащие в РАЗНЫХ группах, схлопнулись бы по общей базе
    "K474-0003".

    Если под правило подходит больше одной группы — НЕ угадываем: возвращаем
    пусто и пишем предупреждение. Молча вычесть у не той группы хуже, чем не
    вычесть вовсе.
    """
    raw = _canon_sku(m_sku)
    sku = _normalize_sku(m_sku)
    if not sku:
        return None, None

    base = _sku_base(sku)
    verbatim, exact, by_base = [], [], []
    for gid, g in groups.items():
        linked_raw = {_canon_sku(s) for s in (g.get("linked_skus") or [])}
        linked_raw.discard("")
        linked = {_normalize_sku(s) for s in linked_raw}
        linked.discard("")
        if raw in linked_raw:
            verbatim.append((gid, g))
        elif sku in linked:
            exact.append((gid, g))
        elif base in linked or any(_sku_base(s) == sku for s in linked):
            by_base.append((gid, g))

    hits = verbatim or exact or by_base
    if not hits:
        return None, None
    if len(hits) > 1:
        logger.warning(
            "⚠️ [AutoDeduction] Артикул %s подходит сразу к %d Мастер-Группам (%s) — "
            "списание пропущено, чтобы не вычесть у чужой. Разведите привязки.",
            m_sku, len(hits), ", ".join(str(g.get("group_name")) for _, g in hits),
        )
        return None, None
    return hits[0]


def _claim_key(order_id: str, sku: str) -> str:
    """Ключ брони — по артикулу целиком, вместе с припиской.

    На нормализованном ключе позиции "H422-0007-9" и "H422-0007-9 2шт" одного
    заказа давали одну и ту же строку, и вторая считалась уже списанной.
    """
    return f"{order_id}:{_canon_sku(sku)}"


def _legacy_claim_key(order_id: str, sku: str) -> str:
    """Ключ, каким он писался до 14.08 (артикул без приписки)."""
    return f"{order_id}:{_normalize_sku(sku)}"


def claim_stock_deduction(
    store_id: Optional[str], order_id: str, claimed_by: str, payload: dict, sku: str = ""
) -> bool:
    """
    Забронировать право списать остаток по ПОЗИЦИИ заказа.

    Раньше право бралось на заказ целиком, и заказ из двух разных товаров
    списывал только первый — вторая Мастер-Группа о продаже не узнавала вовсе.
    Теперь ключ — пара (заказ, артикул), поэтому каждая позиция уменьшает свою
    группу, и при этом повторно не списывается.

    Совместимость: заказы, забронированные старым способом (ключ = только номер
    заказа либо артикул без приписки), по-прежнему считаются списанными. Без
    этого смена формата ключа заново вычла бы все исторические заказы — ровно
    то дублирование, которого мы избегаем.

    Возвращает True, если право получено (значит, списывать должен вызывающий).
    """
    if not store_id or not order_id:
        return True
    from backend.state_helpers import _load_processed_orders_by_kind, _save_processed_orders_by_kind

    claimed = _load_processed_orders_by_kind(store_id, "stock")
    key = _claim_key(order_id, sku) if sku else order_id
    prev = claimed.get(key)
    if prev is None and key != order_id:
        # старые форматы: артикул без приписки, затем право на заказ целиком
        prev = claimed.get(_legacy_claim_key(order_id, sku)) or claimed.get(order_id)
    if prev is not None:
        logger.info(
            "🔁 [StockClaim] Позиция %s уже списана (%s) — %s только логирует, повторного вычитания нет.",
            key, (prev or {}).get("claimed_by", "?"), claimed_by,
        )
        return False
    _save_processed_orders_by_kind(
        {key: {**payload, "claimed_by": claimed_by,
               "claimed_at": datetime.datetime.now().isoformat()}},
        store_id, "stock",
    )
    return True


def _calculate_physical_units(merchant_sku: str, quantity: int = 1, per_unit: int = 1) -> int:
    """
    Физическое количество штук по позиции заказа: заказанное количество,
    умноженное на размер комплекта группы (`deduct_per_unit`, по умолчанию 1).

    `per_unit` задаётся руками в карточке Мастер-Группы — это единственный
    источник размера комплекта. Из артикула он больше не выводится:

    До 14.08 хвост артикула после последнего дефиса считался размером
    упаковки: "T219-0005-4" -> множитель 4. Правило описано в
    REAL_IMPORTANT/поиск_новых_заказов/AUTO_DEDUCTION_ENGINE.md как
    задуманное, но живой каталог Бархана его не подтверждает — хвост там
    номер варианта, а не количество:

      T219-0005-2  Термосумка 20284592_LB4 3 л черный   (одна сумка, списывалось 2)
      H422-0007-9  Подставка под шары 1 шт              (одна штука, списывалось 9)
      H422-0002-4  Палочки для воздушных шаров NG 7 шт  (в пачке 7, списывалось 4)
      K474-0003-3  Кольцо для салфеток 430309, 6 шт     (в пачке 6, списывалось 3)

    Совпадений между хвостом и реальной фасовкой нет ни одного. Заказ
    #1034264977 на 1 шт. "H422-0007-7 2шт" списал 7 штук и увёл группу в
    минус; #1032864996 списал 100 вместо 50. Настоящая фасовка живёт в
    названии товара ("7 шт"), но и оттуда её брать нельзя: рядом стоят
    "Стойка на 19 шаров" и "Термосумка 5.06 л" — вытащить оттуда число
    значит списать 19 стоек за одну. Надёжного способа угадать нет, поэтому
    не угадываем: если у товара действительно комплект, размер должен
    задаваться явно, а не выводиться из артикула.
    """
    return int(quantity) * max(1, int(per_unit or 1))


async def process_packaging_auto_deduction(token: str, store_id: Optional[str] = None) -> dict:
    """
    Обнаружение продаж по вкладкам упаковки и списание остатка Мастер-Групп.

    Смотрит ДВЕ вкладки: «На сборке» (cargo_assembly) и «Ожидает курьера»
    (wait_for_courier, она же «Передача»). Передача — страховка: если заказ
    упаковали между двумя тиками или пока сервис перезагружался, он минует «На
    сборке», и раньше такой заказ не списывался НИКОГДА. Дублей это не создаёт
    — право на списание берётся один раз на пару (заказ, артикул).

    Списываются ВСЕ подходящие позиции заказа, а не только первая: заказ из
    двух товаров разных групп уменьшает обе.

    Выборка двухуровневая (см. комментарий над DEDUCTION_HOT_WINDOW_DAYS):
    узкое окно на каждом тике + полный 13-дневный свип при старте процесса
    и раз в час. Это сохраняет гарантию прежнего поведения — любой заказ
    в KASPI_DELIVERY/ACCEPTED_BY_MERCHANT не старше 13 дней будет увиден,
    пусть в худшем случае с задержкой до одного интервала свипа, — убрав
    ~99% объёма запросов к /orders.
    """
    from backend.state_helpers import _load_processed_packaging_orders, _save_processed_packaging_orders

    now_mono = time.monotonic()
    last_sweep = _last_full_sweep_at.get(store_id)
    full_sweep = last_sweep is None or (now_mono - last_sweep) >= DEDUCTION_FULL_SWEEP_INTERVAL_SECONDS

    try:
        # Синхронный постраничный обход Kaspi MC — тик каждые 30с на каждый
        # активный стор. Узкое окно на горячем пути, 13 дней на свипе.
        days = FULL_SWEEP_WINDOW_DAYS if full_sweep else DEDUCTION_HOT_WINDOW_DAYS
        tabs = await asyncio.to_thread(KaspiAPI.get_all_packaging_tabs, token, days)
    except Exception as e:
        logger.error(f"[AutoDeduction] Error fetching packaging tabs: {e}")
        return {"success": False, "error": str(e)}

    if full_sweep:
        # Только после успешной выборки: упавший свип ретрается следующим тиком.
        _last_full_sweep_at[store_id] = time.monotonic()

    orders = list(tabs.get("cargo_assembly") or []) + list(tabs.get("wait_for_courier") or [])
    if not orders:
        return {"success": True, "deducted_orders_count": 0, "deductions": []}

    processed_orders = await asyncio.to_thread(_load_processed_packaging_orders, store_id)
    new_deductions = []
    # Одна веерная рассылка на группу, даже если заказ задел её дважды.
    to_broadcast: dict = {}

    async with get_stock_lock(store_id):
        groups = await asyncio.to_thread(_load_master_stock_groups, store_id=store_id)
        active_groups = {gid: g for gid, g in groups.items() if g.get("is_active", True)}
        groups_modified = _sweep_stuck_statuses(active_groups)
        orders_marked = False

        for order in orders:
            order_code = str(order.get("code") or order.get("id") or "").strip()
            if not order_code:
                continue

            for item in order.get("items", []) or []:
                m_sku = str(item.get("merchant_sku", "")).strip()
                title = str(item.get("title", "")).strip()
                if not m_sku:
                    continue

                pkey = _claim_key(order_code, m_sku)
                # Отметка старого формата (на заказ целиком) тоже закрывает
                # позицию — исторические заказы не пересчитываются.
                if pkey in processed_orders or order_code in processed_orders:
                    continue

                gid, matched_group = match_group_for_sku(m_sku, active_groups)
                if not matched_group:
                    continue

                ordered = int(item.get("quantity", 1) or 1)
                per_unit = max(1, int(matched_group.get("deduct_per_unit", 1) or 1))
                units = _calculate_physical_units(m_sku, ordered, per_unit)

                if not await asyncio.to_thread(
                    claim_stock_deduction, store_id, order_code, "packaging",
                    {"stage": "packaging", "sku": m_sku, "units": units}, m_sku,
                ):
                    processed_orders[pkey] = {
                        "deducted_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "group_id": matched_group.get("group_id"),
                        "sku": m_sku,
                        "units": 0,
                        "skipped_reason": "already_claimed",
                    }
                    orders_marked = True
                    continue

                old_stock = int(matched_group.get("shared_stock", 0))
                new_stock = max(0, old_stock - units)
                # Сколько штук заказали сверх того, что числилось. max(0, ...)
                # обрезает остаток по нулю — иначе отрицательное число утечёт
                # в оверрайд каталога и в Kaspi. Но сам факт нехватки терялся
                # прямо здесь: журнал показывал "было 4 → стало 0" и ни слова
                # о том, что трёх штук нет. Копим долг отдельным полем.
                shortfall = max(0, units - old_stock)
                debt = int(matched_group.get("oversold", 0)) + shortfall
                matched_group["shared_stock"] = new_stock
                matched_group["oversold"] = debt
                matched_group["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # Статус, который видно на карточке группы, пока идёт списание
                # и следующая за ним веерная рассылка.
                matched_group["status"] = "deducting"
                matched_group["status_since"] = datetime.datetime.now().isoformat()

                now_time = datetime.datetime.now().strftime("%H:%M:%S")
                tail = ""
                if shortfall:
                    tail = f", не хватило {shortfall} шт. — продано больше, чем числилось"
                    logger.warning(
                        "🚨 [AutoDeduction] Перепродажа: заказ #%s списал %d шт. у группы '%s' "
                        "при остатке %d. Не хватает %d шт. (всего долг %d).",
                        order_code, units, matched_group.get("group_name"),
                        old_stock, shortfall, debt,
                    )
                # Комплект показываем прямо в журнале: иначе "заказали 1 —
                # списали 7" снова выглядит как поломка, а не как настройка.
                pack = f" [заказано {ordered} × комплект {per_unit}]" if per_unit > 1 else ""
                log_entry = (
                    f"[{now_time}] 🛒 Списание по заказу #{order_code} ({m_sku}): "
                    f"-{units} шт.{pack} (было {old_stock} → стало {new_stock}{tail})"
                )
                logs = matched_group.setdefault("sync_logs", [])
                logs.insert(0, log_entry)
                matched_group["sync_logs"] = logs[:30]
                matched_group["last_sync_log"] = log_entry

                groups_modified = True
                orders_marked = True

                processed_orders[pkey] = {
                    "deducted_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "group_id": matched_group.get("group_id"),
                    "sku": m_sku,
                    "units": units,
                    "old_stock": old_stock,
                    "new_stock": new_stock,
                    "shortfall": shortfall,
                }

                new_deductions.append({
                    "order_code": order_code,
                    "group_id": matched_group.get("group_id"),
                    "group_name": matched_group.get("group_name"),
                    "sku": m_sku,
                    "units": units,
                    "new_stock": new_stock,
                    "shortfall": shortfall,
                })

                logger.info(
                    f"⚡ [AutoDeduction] Заказ #{order_code} списал {units} шт. "
                    f"у группы '{matched_group.get('group_name')}'. Остаток: {new_stock}"
                    + (f" (долг {debt} шт.)" if debt else "")
                )

                if shortfall:
                    asyncio.create_task(notify_oversold_telegram(
                        group_name=matched_group.get("group_name") or "Без названия",
                        group_id=matched_group.get("group_id") or gid,
                        store_id=store_id,
                        order_code=order_code,
                        sku=m_sku,
                        units=units,
                        old_stock=old_stock,
                        debt=debt,
                    ))

                to_broadcast[matched_group.get("group_id") or gid] = {
                    "order_code": order_code,
                    "sku": m_sku,
                    "units": units,
                    "title": title or "Товар",
                    "customer": order.get("customer") or "Покупатель",
                }

        if groups_modified:
            await asyncio.to_thread(_save_master_stock_groups, groups, store_id=store_id)
        if groups_modified or orders_marked:
            # Отметку сохраняем и когда списания не было (право уже занято) —
            # иначе та же позиция переспрашивалась бы каждые 30 секунд.
            await asyncio.to_thread(_save_processed_packaging_orders, processed_orders, store_id)

    for gid, trigger_info in to_broadcast.items():
        asyncio.create_task(
            execute_broadcast_sync(gid, token, store_id=store_id, trigger_order_info=trigger_info)
        )

    return {
        "success": True,
        "deducted_orders_count": len(new_deductions),
        "deductions": new_deductions,
    }


async def run_packaging_sync_cycle():
    """
    Один цикл авто-списания по всем активным сторам. Вынесено отдельной
    функцией для юнит-тестов.

    Слайс 7: легаси-фоллбек на общую сессию (когда в БД ещё нет ни одного
    Store) убран — нет активных сторов, нечего списывать.

    Автосборка (принятие/упаковка/накладные) намеренно повешена на этот же
    тик, а не на отдельный луп — второй независимый постраничный обход
    /orders на Kaspi поверх и без того горячего пути не заводим (см.
    HANDOFF.md §3.0). Для сторов без единого включённого правила
    run_auto_assembly_cycle делает ранний выход без единого запроса к Kaspi.
    """
    from backend.services.auto_assembly_worker import run_auto_assembly_cycle

    from backend import feature_flags

    stores = await list_active_stores()
    for store in stores:
        try:
            if await asyncio.to_thread(feature_flags.is_stock_groups_enabled, store.store_id):
                await process_packaging_auto_deduction(store.token, store.store_id)
        except Exception as e:
            logger.error(f"[Packaging Sync Loop] Store {store.store_id} failed: {e}")
        try:
            from backend.billing import enforcement as billing_enforcement
            blocked, _reason = await asyncio.to_thread(billing_enforcement.is_service_blocked, store.store_id)
            if not blocked and await asyncio.to_thread(feature_flags.is_autoassembly_enabled, store.store_id):
                await run_auto_assembly_cycle(store.token, store.store_id)
        except Exception as e:
            logger.error(f"[Auto Assembly] Store {store.store_id} failed: {e}")

async def start_packaging_sync_loop(interval_seconds: int = 30):
    """Фоновый воркер авто-списания остатков при появлении новых заказов в Упаковке — по всем активным сторам (Phase 7)."""
    logger.info("🚀 Запуск фонового воркера авто-списания остатков по Упаковке...")
    while True:
        try:
            await run_packaging_sync_cycle()
        except asyncio.CancelledError:
            logger.info("⏹ [Packaging Sync Loop] Воркер авто-списания остановлен.")
            break
        except Exception as e:
            logger.error(f"❌ [Packaging Sync Loop] Ошибка: {e}")

        await asyncio.sleep(interval_seconds)

