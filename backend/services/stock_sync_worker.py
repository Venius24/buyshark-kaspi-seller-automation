import asyncio
import logging
import random
import datetime
import os
import json
import requests
from typing import Optional, List

from backend.state_helpers import (
    _get_token,
    _load_master_stock_groups,
    _save_master_stock_groups,
    _load_processed_orders,
    _save_processed_orders,
    _load_overrides,
    _save_override,
)
from backend.kaspi_wrapper import KaspiAPI
from backend.dumper.executor import RateLimitExecutorWorker, _load_mc_cookies
from backend.dumper.config import discover_merchant_id, _decode_base64_if_needed
from backend.dumper.telemetry import telemetry_manager
from backend.services.webhook_dispatcher import dispatch_webhook_event


logger = logging.getLogger(__name__)

# Global lock for thread/async-safe group stock operations
stock_lock = asyncio.Lock()

async def notify_zero_stock_telegram(group_name: str, group_id: str, linked_skus: List[str], trigger_order_info: Optional[dict] = None):
    """Отправка Push/Telegram уведомления при обнулении остатка группы с деталями о заказе-триггере."""
    try:
        from backend.chatbot.config import load_chatbot_config
        cfg = load_chatbot_config()
        tg_token = cfg.get("tg_bot_token")
        admin_ids = cfg.get("admin_ids", [])
        
        if not tg_token or not admin_ids:
            logger.info(f"Zero Stock Telegram notify skipped: token or admin_ids missing.")
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
                f"🛒 <b>ПРИЧИНА СНИЖЕНИЯ ДО 0 (Новый заказ):</b>\n"
                f"• <b>№ Заказа:</b> <code>#{code}</code>\n"
                f"• <b>Товар:</b> {title}\n"
                f"• <b>Артикул списания:</b> <code>{sku}</code> (-{units} шт.)\n"
                f"• <b>Покупатель:</b> {customer}\n\n"
            )

        text = (
            f"🚨 <b>ВНИМАНИЕ: СНЯТО С ПРОДАЖИ (Zero Stock Protection)</b>\n\n"
            f"📦 <b>Группа:</b> {group_name}\n"
            f"🆔 <b>ID Группы:</b> <code>{group_id}</code>\n"
            f"📉 <b>Доступный остаток:</b> 0 шт.\n"
            f"🔗 <b>Привязанные SKU:</b> {skus_str}\n\n"
            f"{order_section}"
            f"⚠️ Все карточки товаров группы занулены и временно сняты с продажи на Kaspi!"
        )

        tg_proxy = cfg.get("tg_bot_proxy") or cfg.get("tg_proxy")
        proxies = {"https": tg_proxy, "http": tg_proxy} if tg_proxy else None

        for admin_id in admin_ids:
            url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
            payload = {
                "chat_id": admin_id,
                "text": text,
                "parse_mode": "HTML"
            }
            try:
                requests.post(url, json=payload, proxies=proxies, timeout=5)
            except Exception as ex:
                logger.error(f"Error sending Telegram zero-stock alert to {admin_id}: {ex}")
    except Exception as e:
        logger.error(f"Failed to execute notify_zero_stock_telegram: {e}")

async def broadcast_ws_event(event_type: str, data: dict):
    """Вещание WebSocket события на клиентские UI-подключения."""
    try:
        await telemetry_manager.broadcast(event_type, data)
    except Exception as e:
        logger.error(f"Error broadcasting WS event {event_type}: {e}")


async def execute_broadcast_sync(group_id: str, token: str, trigger_order_info: Optional[dict] = None):
    """
    Веерная рассылка обновленного остатка по всем SKU группы через 3-шаговый Kaspi MC API.
    """
    async with stock_lock:
        groups = _load_master_stock_groups()
        group = groups.get(group_id)
        if not group or not group.get("is_active", True):
            return

        group["status"] = "updating"
        group["last_sync_log"] = "Запуск веерной синхронизации..."
        _save_master_stock_groups(groups)
        
    await broadcast_ws_event("stock_group_updated", group)

    linked_skus = group.get("linked_skus", [])
    shared_stock = int(group.get("shared_stock", 0))
    group_name = group.get("group_name", "Без названия")

    logger.info(f"🔄 [Broadcast Sync] Начало обхода {len(linked_skus)} SKU для группы '{group_name}' (Остаток: {shared_stock})")

    # Загружаем товары из кэша
    products_file = "states/products.json"
    all_products = []
    if os.path.exists(products_file):
        try:
            with open(products_file, "r", encoding="utf-8") as f:
                all_products = json.load(f)
        except Exception:
            all_products = []

    cookies = _load_mc_cookies()
    has_cookies = bool(cookies and "mc-session" in cookies and "mc-sid" in cookies)
    merchant_id = discover_merchant_id(token) or "20186986"

    success_count = 0
    fail_count = 0
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
        status_text = "Активен" if shared_stock > 0 else "Архив"

        # Сохраняем локальный оверрайд
        _save_override(sku, price, shared_stock, target_p.get("brand"), status_text)

        # Обновляем в файле products.json
        for p in all_products:
            if p.get("sku") == sku or p.get("merchantSku") == sku or p.get("kaspiSku") == sku:
                p["stock"] = shared_stock
                p["status"] = status_text
                break

        if has_cookies:
            # Запуск 3-шагового частного API Kaspi
            success, val = await asyncio.to_thread(
                RateLimitExecutorWorker._update_kaspi_price_sync,
                merchant_uid=merchant_id,
                cookies=cookies,
                sku=target_p.get("merchantSku") or sku,
                master_sku=decoded_master,
                model_name=target_p.get("name", "Товар"),
                new_price=price,
                stock=shared_stock,
                store_id_suffix=store_suffix,
                city_id="750000000"
            )
            if success:
                success_count += 1
                msg = f"[{t_now}] ✓ Группа '{group_name}' -> SKU {sku}: остаток {shared_stock} отправлен в Kaspi MC (Tx: {val})"
                logs.append(msg)
                await broadcast_ws_event("log", msg)
            else:
                fail_count += 1
                msg = f"[{t_now}] ❌ Группа '{group_name}' -> SKU {sku}: ошибка Kaspi MC: {val}"
                logs.append(msg)
                await broadcast_ws_event("log", msg)
        else:
            fail_count += 1
            msg = f"[{t_now}] ⚠️ Группа '{group_name}' -> SKU {sku}: отсутствуют куки Kaspi MC (требуется SMS-авторизация)"
            logs.append(msg)
            await broadcast_ws_event("log", msg)

        # Jitter пауза 1.2 – 2.0 секунды перед следующим SKU
        if idx < len(linked_skus) - 1:
            jitter_delay = random.uniform(1.2, 2.0)
            await asyncio.sleep(jitter_delay)

    # Сохраняем обновленный products.json
    if all_products:
        try:
            with open(products_file, "w", encoding="utf-8") as f:
                json.dump(all_products, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # Итоговое обновление статуса группы
    async with stock_lock:
        groups = _load_master_stock_groups()
        if group_id in groups:
            g = groups[group_id]
            g["last_synced_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            g["last_sync_log"] = f"Успешно: {success_count}/{len(linked_skus)}. Ошибок: {fail_count}."
            if fail_count == 0:
                g["status"] = "synced"
            elif success_count > 0:
                g["status"] = "warning"
            else:
                g["status"] = "error"

            g["sync_logs"] = logs
            _save_master_stock_groups(groups)
            await broadcast_ws_event("stock_group_updated", g)

    # Обработка Zero Stock Protection
    if shared_stock == 0:
        logger.warning(f"🚨 [Zero Stock Protection] Группа '{group_name}' занулена!")
        await notify_zero_stock_telegram(group_name, group_id, linked_skus, trigger_order_info)
        await broadcast_ws_event("zero_stock_alert", {
            "group_id": group_id,
            "group_name": group_name,
            "message": f"Группа [{group_name}] занулена и снята с продажи на Kaspi!"
        })

async def master_stock_radar_loop():
    """
    Фоновый радар фиксации продаж заказов и веерной синхронизации остатков.
    """
    logger.info("🟢 [Master Stock Radar] Запущен фоновый радар списания остатков.")
    while True:
        try:
            await asyncio.sleep(20)

            token = _get_token()
            if not token:
                continue

            groups = _load_master_stock_groups()
            active_groups = {gid: g for gid, g in groups.items() if g.get("is_active", True)}
            if not active_groups:
                continue

            # Получаем свежие новые заказы через Kaspi API
            try:
                orders_resp = await asyncio.to_thread(
                    KaspiAPI.get_orders_with_products,
                    token,
                    state="NEW",
                    status="APPROVED_BY_BANK",
                    limit=50
                )
            except Exception as req_ex:
                logger.debug(f"[Master Stock Radar] Ошибка получения заказов: {req_ex}")
                continue

            if not orders_resp:
                continue

            # Загружаем товары из кэша для резолва алиасов ключей
            products_file = "states/products.json"
            all_products_cache = []
            if os.path.exists(products_file):
                try:
                    with open(products_file, "r", encoding="utf-8") as f:
                        all_products_cache = json.load(f)
                except Exception:
                    pass

            processed_orders = _load_processed_orders()
            order_dedup_changed = False

            for order in orders_resp:
                order_id = str(order.get("id") or order.get("internalId") or "")
                if not order_id or order_id in processed_orders:
                    continue

                entries = order.get("entries", [])
                if not entries and "sku" in order:
                    entries = [{"sku": order.get("sku"), "quantity": order.get("quantity", 1)}]

                order_matched_groups = set()

                for entry in entries:
                    raw_sku_candidates = set()
                    for k in ["sku", "merchantSku", "masterSku", "kaspiSku"]:
                        val = entry.get(k)
                        if val:
                            raw_sku_candidates.add(str(val).strip())

                    qty = int(entry.get("quantity", 1))
                    if not raw_sku_candidates:
                        continue

                    # Находим товар в кэше и собираем все его возможные алиасы (merchantSku, kaspiSku и т.д.)
                    all_possible_skus = set(raw_sku_candidates)
                    for cand in raw_sku_candidates:
                        p_match = next((p for p in all_products_cache if p.get("sku") == cand or p.get("merchantSku") == cand or p.get("kaspiSku") == cand), None)
                        if p_match:
                            for key_field in ["sku", "merchantSku", "kaspiSku"]:
                                if p_match.get(key_field):
                                    all_possible_skus.add(str(p_match[key_field]).strip())

                    # Находим группы, в которые входит любой из этих ключей
                    for gid, grp in active_groups.items():
                        if gid in order_matched_groups:
                            continue
                        linked = [str(s).strip() for s in grp.get("linked_skus", [])]
                        if any(k in linked for k in all_possible_skus):
                            async with stock_lock:
                                current_stock = int(grp.get("shared_stock", 0))
                                new_stock = max(0, current_stock - qty)
                                grp["shared_stock"] = new_stock
                                groups[gid] = grp
                                _save_master_stock_groups(groups)
                                logger.info(
                                    f"📦 [Order Sale Detected] Заказ {order_id} (Ключи: {list(all_possible_skus)}, qty: {qty}). "
                                    f"Группа '{grp.get('group_name')}' остаток: {current_stock} -> {new_stock}"
                                )

                                # Отправка Webhook в 1С/ERP
                                asyncio.create_task(
                                    dispatch_webhook_event(
                                        "order.created",
                                        {
                                            "order_id": order_id,
                                            "purchased_sku": list(all_possible_skus)[0],
                                            "all_matched_skus": list(all_possible_skus),
                                            "quantity": qty,
                                            "group_id": gid,
                                            "group_name": grp.get("group_name"),
                                            "external_code": grp.get("external_code"),
                                            "previous_stock": current_stock,
                                            "remaining_shared_stock": new_stock
                                        }
                                    )
                                )

                            order_matched_groups.add(gid)

                # Помечаем заказ как обработанный
                processed_orders[order_id] = {
                    "processed_at": datetime.datetime.now().isoformat(),
                    "matched_groups": list(order_matched_groups)
                }
                order_dedup_changed = True

                # Запускаем веерное обновление для затронутых групп
                for gid in order_matched_groups:
                    asyncio.create_task(execute_broadcast_sync(gid, token))

            if order_dedup_changed:
                _save_processed_orders(processed_orders)

        except asyncio.CancelledError:
            logger.info("⏹ [Master Stock Radar] Радар остановлен.")
            break
        except Exception as e:
            logger.error(f"❌ [Master Stock Radar] Ошибка в цикле радара: {e}")


# ── 5. Auto Deduction on Packaging Detection Engine ──────────────────────

def _calculate_physical_units(merchant_sku: str, quantity: int = 1) -> int:
    """
    Вычисляет физическое количество штук товара с учетом комплектов и мультипаков (-2, -4, -6...).
    Например: T219-0005-4 (qty 1) -> 4 шт.
    """
    if not merchant_sku:
        return quantity

    sku_str = str(merchant_sku).strip()
    multiplier = 1
    if "-" in sku_str:
        parts = sku_str.split("-")
        last_part = parts[-1]
        if last_part.isdigit() and int(last_part) > 1 and len(last_part) <= 2:
            multiplier = int(last_part)

    return multiplier * int(quantity)


async def process_packaging_auto_deduction(token: str) -> dict:
    """
    Фоновая процедура обнаружения новых заказов в Упаковке:
    1. Запрашивает активные заказы 'На сборке' (cargo_assembly).
    2. Если заказ новый (нет в processed_packaging_orders.json):
       - Находит соответствующую Мастер-Группу остатка.
       - Вычисляет физическое количество списания с учетом комплектов (-2, -4, -6...).
       - Уменьшает shared_stock группы: max(0, shared_stock - units).
       - Логирует списание и при 0 активирует Zero-Stock Protection.
       - Запускает асинхронный broadcast sync (execute_broadcast_sync) на Kaspi!
    """
    from backend.state_helpers import _load_processed_packaging_orders, _save_processed_packaging_orders

    processed_orders = _load_processed_packaging_orders()

    try:
        tabs = KaspiAPI.get_all_packaging_tabs(token)
    except Exception as e:
        logger.error(f"[AutoDeduction] Error fetching packaging tabs: {e}")
        return {"success": False, "error": str(e)}

    cargo_assembly = tabs.get("cargo_assembly", [])
    if not cargo_assembly:
        return {"success": True, "deducted_orders_count": 0, "deductions": []}

    new_deductions = []

    async with stock_lock:
        groups = _load_master_stock_groups()
        groups_modified = False

        for order in cargo_assembly:
            order_code = str(order.get("code") or order.get("id")).strip()
            if not order_code or order_code in processed_orders:
                continue

            items = order.get("items", [])
            for item in items:
                m_sku = str(item.get("merchant_sku", "")).strip()
                title = str(item.get("title", "")).strip()
                qty = item.get("quantity", 1)

                matched_group = None
                for g_id, g in groups.items():
                    linked = set(str(s).strip().lower() for s in g.get("linked_skus", []))
                    m_sku_lower = m_sku.lower()

                    if (
                        m_sku_lower in linked
                        or any(s in m_sku_lower or m_sku_lower.startswith(s) or s in title.lower() for s in linked if len(s) >= 4)
                    ):
                        matched_group = g
                        break

                if matched_group:
                    units = _calculate_physical_units(m_sku, qty)
                    old_stock = int(matched_group.get("shared_stock", 0))
                    new_stock = max(0, old_stock - units)
                    matched_group["shared_stock"] = new_stock
                    matched_group["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                    now_time = datetime.datetime.now().strftime("%H:%M:%S")
                    log_entry = (
                        f"[{now_time}] 🛒 Авто-списание по заказу #{order_code} ({m_sku}): "
                        f"-{units} шт. (Было: {old_stock} -> Стало: {new_stock} шт.)"
                    )

                    logs = matched_group.setdefault("sync_logs", [])
                    logs.insert(0, log_entry)
                    if len(logs) > 30:
                        matched_group["sync_logs"] = logs[:30]

                    matched_group["last_sync_log"] = log_entry
                    groups_modified = True

                    processed_orders[order_code] = {
                        "deducted_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "group_id": matched_group["group_id"],
                        "sku": m_sku,
                        "units": units,
                        "old_stock": old_stock,
                        "new_stock": new_stock
                    }

                    new_deductions.append({
                        "order_code": order_code,
                        "group_id": matched_group["group_id"],
                        "group_name": matched_group.get("group_name"),
                        "sku": m_sku,
                        "units": units,
                        "new_stock": new_stock
                    })

                    logger.info(f"⚡ [AutoDeduction] Заказ #{order_code} списал {units} шт. у группы '{matched_group.get('group_name')}'. Остаток: {new_stock}")

                    trigger_info = {
                        "order_code": order_code,
                        "sku": m_sku,
                        "units": units,
                        "title": title or item.get("title") or "Товар",
                        "customer": order.get("customer") or "Покупатель"
                    }

                    # Запускаем веерное списание остатка на Kaspi с информацией о заказе
                    asyncio.create_task(execute_broadcast_sync(matched_group["group_id"], token, trigger_info))
                    break

        if groups_modified:
            _save_master_stock_groups(groups)
            _save_processed_packaging_orders(processed_orders)

    return {
        "success": True,
        "deducted_orders_count": len(new_deductions),
        "deductions": new_deductions
    }


async def start_packaging_sync_loop(interval_seconds: int = 30):
    """Фоновый воркер авто-списания остатков при появлении новых заказов в Упаковке."""
    logger.info("🚀 Запуск фонового воркера авто-списания остатков по Упаковке...")
    while True:
        try:
            token = _get_token()
            if token and not token.startswith("test_"):
                await process_packaging_auto_deduction(token)
        except asyncio.CancelledError:
            logger.info("⏹ [Packaging Sync Loop] Воркер авто-списания остановлен.")
            break
        except Exception as e:
            logger.error(f"❌ [Packaging Sync Loop] Ошибка: {e}")

        await asyncio.sleep(interval_seconds)

