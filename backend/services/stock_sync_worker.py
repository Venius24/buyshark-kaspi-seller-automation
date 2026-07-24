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

async def notify_zero_stock_telegram(group_name: str, group_id: str, linked_skus: List[str]):
    """Отправка Push/Telegram уведомления при обнулении остатка группы."""
    try:
        from backend.chatbot.config import load_chatbot_config
        cfg = load_chatbot_config()
        tg_token = cfg.get("tg_bot_token")
        admin_ids = cfg.get("admin_ids", [])
        
        if not tg_token or not admin_ids:
            logger.info(f"Zero Stock Telegram notify skipped: token or admin_ids missing.")
            return

        skus_str = ", ".join(linked_skus[:5]) + ("..." if len(linked_skus) > 5 else "")
        text = (
            f"🚨 <b>ВНИМАНИЕ: СНЯТО С ПРОДАЖИ (Zero Stock Protection)</b>\n\n"
            f"📦 <b>Группа:</b> {group_name}\n"
            f"🆔 <b>ID Группы:</b> <code>{group_id}</code>\n"
            f"📉 <b>Доступный остаток:</b> 0 шт.\n"
            f"🔗 <b>Привязанные SKU:</b> {skus_str}\n\n"
            f"⚠️ Все карточки товаров группы занулены и временно сняты с продажи на Kaspi!"
        )

        use_proxy = cfg.get("use_tg_proxy")
        tg_proxy = cfg.get("tg_proxy")
        proxies = {"https": tg_proxy} if (use_proxy and tg_proxy) else None

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


async def execute_broadcast_sync(group_id: str, token: str):
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

    for idx, sku in enumerate(linked_skus):
        # Находим товар в ассортименте
        target_p = next((p for p in all_products if p.get("sku") == sku or p.get("merchantSku") == sku), None)
        if not target_p:
            target_p = {"sku": sku, "merchantSku": sku, "name": f"Товар {sku}", "price_num": 1000}

        price = int(target_p.get("price_num") or target_p.get("price", 0) or 1000)
        master_sku = target_p.get("kaspiSku") or target_p.get("sku") or sku
        decoded_master = _decode_base64_if_needed(master_sku)

        store_full_id = target_p.get("storeId") or f"{merchant_id}_001"
        store_suffix = store_full_id.split("_")[1] if "_" in store_full_id else "001"
        status_text = "Активен" if shared_stock > 0 else "Архив"

        # Сохраняем локальный оверрайд
        _save_override(sku, price, shared_stock, target_p.get("brand"), status_text)

        # Обновляем в файле products.json
        for p in all_products:
            if p.get("sku") == sku:
                p["stock"] = shared_stock
                p["status"] = status_text
                break

        if has_cookies:
            # Запуск 3-шагового частного API Kaspi
            # Передаем stock=shared_stock
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
                logs.append(f"✓ SKU {sku}: остаток {shared_stock} отправлен в Kaspi (Tx: {val})")
            else:
                fail_count += 1
                logs.append(f"✗ SKU {sku}: ошибка отправки ({val})")
        else:
            success_count += 1
            logs.append(f"✓ SKU {sku}: остаток {shared_stock} обновлен локально (MC cookies отсутствуют)")

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

    # Обновляем состояние группы
    async with stock_lock:
        groups = _load_master_stock_groups()
        if group_id in groups:
            g = groups[group_id]
            if fail_count == 0:
                g["status"] = "synced"
            elif success_count > 0:
                g["status"] = "synced"
            else:
                g["status"] = "error"

            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            g["last_synced_at"] = now_str
            g["last_sync_log"] = f"Успешно: {success_count}/{len(linked_skus)}. Ошибок: {fail_count}."
            _save_master_stock_groups(groups)
            group_to_broadcast = g
        else:
            group_to_broadcast = None

    if group_to_broadcast:
        await broadcast_ws_event("stock_group_updated", group_to_broadcast)

    # Обработка Zero Stock Protection
    if shared_stock == 0:
        logger.warning(f"🚨 [Zero Stock Protection] Группа '{group_name}' занулена!")
        await notify_zero_stock_telegram(group_name, group_id, linked_skus)
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
                logger.debug(f"[Master Stock Radar] Ошибка получения заказов: {req_req_ex if 'req_req_ex' in locals() else req_ex}")
                continue

            if not orders_resp:
                continue

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
                    sku = str(entry.get("sku") or entry.get("merchantSku") or "")
                    qty = int(entry.get("quantity", 1))

                    if not sku:
                        continue

                    # Находим группы, в которые входит данный SKU
                    for gid, grp in active_groups.items():
                        linked = grp.get("linked_skus", [])
                        if sku in linked:
                            async with stock_lock:
                                current_stock = int(grp.get("shared_stock", 0))
                                new_stock = max(0, current_stock - qty)
                                grp["shared_stock"] = new_stock
                                groups[gid] = grp
                                _save_master_stock_groups(groups)
                                logger.info(
                                    f"📦 [Order Sale Detected] Заказ {order_id} (SKU: {sku}, qty: {qty}). "
                                    f"Группа '{grp.get('group_name')}' остаток: {current_stock} -> {new_stock}"
                                )

                                # Отправка Webhook в 1С/ERP
                                asyncio.create_task(
                                    dispatch_webhook_event(
                                        "order.created",
                                        {
                                            "order_id": order_id,
                                            "purchased_sku": sku,
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
