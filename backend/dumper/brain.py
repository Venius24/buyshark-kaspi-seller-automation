import logging
from dataclasses import replace
from datetime import datetime
from typing import List, Dict, Any, Optional
from backend.dumper.config import DumpingProductConfig
from backend.dumper import guard

logger = logging.getLogger(__name__)

# Вопрос менеджеру о продавце без рейтинга — только если решение сдвигает
# нашу цену хотя бы на столько.
ASK_MIN_IMPACT_PCT = 2.0
ASK_MIN_IMPACT_TENGE = 5.0


class DumpingEngine:
    @staticmethod
    def filter_offers(config: DumpingProductConfig, my_merchant_id: str, sellers_offers: List[dict]) -> List[dict]:
        """
        Фильтрует список предложений продавцов с учетом настроек фильтрации:
        - Черный список продавцов (blacklisted_merchants)
        - Минимальный рейтинг продавца (min_seller_rating)
        - Максимальное время доставки в днях / предзаказ (max_delivery_days)
        - Доставка только в свой город (same_city_only)

        До 2026-10-03 рейтинг, дни доставки и город читались из полей
        `rating`/`deliveryDays`/`cityCode`, которых в ответе Kaspi offer-view
        нет — фильтры молча не отсеивали никого. Теперь поля разбирает
        guard.normalize_offer (merchantRating, deliveryOptions.*.deliveryDays,
        deliveryOptions.*.interCity). Наш собственный оффер не фильтруется
        никогда — иначе потерялась бы наша позиция.
        """
        if not sellers_offers:
            return []

        blacklist = set(config.blacklisted_merchants or [])
        cleaned = []

        for offer in sellers_offers:
            merchant_id = str(offer.get("merchantId", ""))
            merchant_name = str(offer.get("merchantName", ""))
            is_me = merchant_id == str(my_merchant_id)

            # 1. Проверка черного списка
            if not is_me and (merchant_id in blacklist or merchant_name in blacklist):
                continue

            # 2. Очистка цены
            price_num = guard.parse_price(offer.get("price"))
            if price_num is None:
                continue

            norm = guard.normalize_offer(offer, config.city_code)

            if not is_me:
                # 3. Фильтр по рейтингу продавца
                if config.min_seller_rating is not None and norm["rating"] is not None:
                    if norm["rating"] < config.min_seller_rating:
                        continue

                # 4. Фильтр по дням доставки (предзаказы)
                if config.max_delivery_days is not None and norm["delivery_days"] is not None:
                    if norm["delivery_days"] > config.max_delivery_days:
                        continue

                # 5. Фильтр по городу
                if config.same_city_only and norm["intercity"] is True:
                    continue

            norm["price"] = price_num
            cleaned.append({
                "merchantId": merchant_id,
                "merchantName": merchant_name,
                "price": price_num,
                "norm": norm,
                "raw_offer": offer
            })

        return cleaned

    @staticmethod
    def calculate_new_price(
        config: DumpingProductConfig,
        my_merchant_id: str,
        sellers_offers: List[dict],
        guard_cfg: Optional["guard.GuardConfig"] = None,
        guard_ctx: Optional[dict] = None,
    ) -> dict:
        """
        guard_cfg/guard_ctx — защита от оттеснения (backend/dumper/guard.py).
        guard_ctx: {"moves": [наши ходы по SKU], "peak_24h": float|None, "now": datetime}.
        Без guard_cfg (или enabled=False) поведение прежнее, кроме разбора
        лесенки для отчёта — он идёт всегда, если есть история.

        Главный калькулятор цены с поддержкой 5 режимов демпинга:
        - OFF: выключен
        - TOP_1: 1-е место
        - TOP_2: 2-е место
        - MATCH_FIRST: прижиматься к 1-му месту (-1 ₸ или уход в min_price)
        - TARGET_POSITION: N-е место
        """
        # Если режим OFF или конфигурация выключена
        if not config.enabled or config.mode == "OFF":
            return {
                "targetPrice": config.my_current_price,
                "currentPos": None,
                "bestCompetitorPrice": None,
                "firstPrice": config.my_current_price,
                "action": "DISABLED",
                "dumping_status": "OFF"
            }

        # Офферы не получены, радар подставил лучшую цену со страницы товара
        # (заглушка COMPETITOR_TOP_1). Если она не ниже нашей — это, скорее всего,
        # мы сами: до 04.10.2026 демпинг в этом случае снижал цену под себя же.
        stubs = [o for o in sellers_offers or [] if guard.is_radar_stub(o.get("merchantId"))]
        if stubs and len(stubs) == len(sellers_offers):
            stub_price = min((guard.parse_price(o.get("price")) for o in stubs), key=lambda p: p or 1e18)
            if stub_price is None or stub_price >= config.my_current_price:
                return {
                    "targetPrice": config.my_current_price,
                    "currentPos": 1,
                    "bestCompetitorPrice": None,
                    "firstPrice": config.my_current_price,
                    "action": "HOLD_POSITION_NO_DATA",
                    "dumping_status": "WINNING_1ST",
                }

        gcfg = guard_cfg if guard_cfg is not None else guard.GuardConfig()
        gctx = guard_ctx or {}

        # Режим наблюдения: всё считаем как с защитой и показываем в отчёте
        # («защита поставила бы X»), но цену отдаём такую, как без неё.
        if gcfg.enabled and gcfg.observe_only:
            live = DumpingEngine.calculate_new_price(
                config, my_merchant_id, sellers_offers, replace(gcfg, observe_only=False), gctx)
            plain = DumpingEngine.calculate_new_price(
                config, my_merchant_id, sellers_offers, replace(gcfg, enabled=False), gctx)
            g = dict(live.get("guard") or {})
            # «Поставила бы» — только если защита расходится с обычным демпингом.
            differs = abs(float(live["targetPrice"]) - float(plain["targetPrice"])) >= 1.0
            g.update({
                "observe": True,
                "would": {"price": live["targetPrice"], "action": live["action"]} if differs else None,
                "limit": None, "event": None, "alert": False,
            })
            plain["guard"] = g
            return plain

        cleaned_offers = DumpingEngine.filter_offers(config, my_merchant_id, sellers_offers)

        # -------------------------------------------------------------
        # Защита от оттеснения: кого из соперников не учитывать
        # -------------------------------------------------------------
        now = gctx.get("now") or datetime.utcnow()
        decisions = gctx.get("decisions") or {}
        all_moves = gctx.get("moves") or []
        step_for_ladder = config.step if config.step > 0 else 10.0
        ladder = guard.detect_ladder(guard.moves_since_reset(all_moves), gcfg, step_for_ladder, now)
        ladder_active = gcfg.enabled and guard.ladder_is_active(ladder, now)
        my_norm = next((o["norm"] for o in cleaned_offers if o["merchantId"] == str(my_merchant_id)), None)
        # Место на карточке — по всем продавцам, включая отсеянных.
        rival_prices = [
            guard.parse_price(o.get("price")) for o in sellers_offers or []
            if str(o.get("merchantId", "")) != str(my_merchant_id)
        ]
        card_rank = 1 + sum(1 for rp in rival_prices if rp is not None and rp < config.my_current_price)
        ignored = []
        asks = []
        if gcfg.enabled:
            considered = []
            for o in cleaned_offers:
                if o["merchantId"] == str(my_merchant_id):
                    considered.append(o)
                    continue
                decision = decisions.get(o["merchantId"])
                reasons = guard.weak_reasons(o["norm"], gcfg, my_norm, now, decision)
                if (ladder_active and gcfg.ladder_response == guard.LADDER_RETREAT
                        and ladder.rival_id and o["merchantId"] == ladder.rival_id):
                    reasons.append(guard.REASON_LADDER_RETREAT)
                if reasons:
                    ignored.append({**o["norm"], "reasons": reasons})
                else:
                    considered.append(o)
                    # Без рейтинга — кандидат в вопрос менеджеру. Останется
                    # вопросом, только если решение меняет нашу цену (ниже).
                    if guard.needs_decision(o["norm"], gcfg, decision):
                        asks.append(dict(o["norm"]))
            cleaned_offers = considered
        drop = guard.drop_state(all_moves, gcfg, config.my_current_price, now)
        paused = bool(gcfg.enabled and drop.pause_until and now < drop.pause_until)
        cooling_until = guard.cooling_hold_until(all_moves, gcfg)
        night = bool(gcfg.enabled and gcfg.night_freeze and guard.is_night(gcfg, now))
        guard_info = {
            "enabled": gcfg.enabled,
            "observe": False,
            "ignored": sorted(ignored, key=lambda x: x["price"]),
            "asks": asks,
            "ladder": ladder.to_dict() if ladder else None,
            "ladder_active": ladder_active,
            "last_reset_at": (lambda t: t.isoformat() if t else None)(
                guard.last_event_at(all_moves, guard.ACTION_LADDER_RESET)),
            "pause_until": drop.pause_until.isoformat() if paused else None,
            "cooling_until": cooling_until.isoformat() if cooling_until and now < cooling_until else None,
            "night": night,
            "card_rank": card_rank,
            "limit": None,
            "event": None,
            "alert": False,
        }

        # Сортировка предложений по возрастанию цены
        sorted_offers = sorted(cleaned_offers, key=lambda x: x["price"]) if cleaned_offers else []

        # Поиск нашей текущей позиции среди отфильтрованных
        my_pos = None
        for idx, offer in enumerate(sorted_offers, start=1):
            if offer.get("merchantId") == str(my_merchant_id):
                my_pos = idx
                break

        # Список чисто конкурентов (без нас)
        competitors = [o for o in sorted_offers if o.get("merchantId") != str(my_merchant_id)]

        # Случай 0: Конкурентов нет
        if not competitors:
            # Если предложения не удалось загрузить (ошибка API / пустой ответ) — сохраняем текущую цену
            if not sellers_offers:
                return {
                    "targetPrice": config.my_current_price,
                    "currentPos": 1,
                    "bestCompetitorPrice": None,
                    "firstPrice": config.my_current_price,
                    "action": "HOLD_POSITION_NO_DATA",
                    "dumping_status": "WINNING_1ST"
                }

            # Все соперники отсеяны защитой — это не «мы одни на карточке»:
            # отсеянный провокатор по-прежнему дешевле. По умолчанию держим цену.
            if ignored and gcfg.all_ignored_action == guard.ALL_IGNORED_HOLD:
                return {
                    "targetPrice": config.my_current_price,
                    "currentPos": 1,
                    "bestCompetitorPrice": None,
                    "firstPrice": config.my_current_price,
                    "action": "GUARD_ALL_IGNORED_HOLD",
                    "dumping_status": "WINNING_1ST",
                    "guard": guard_info,
                    "rival": None,
                }

            # Если предложения загрузились успешно, но в них действительно 0 конкурентов (мы единственные)
            target_price = config.max_price if config.max_price > 0 else config.my_current_price
            return {
                "targetPrice": target_price,
                "currentPos": 1,
                "bestCompetitorPrice": None,
                "firstPrice": target_price,
                "action": "SET_MAX_PRICE_SINGLE_SELLER",
                "dumping_status": "MAX_PRICE_SINGLE",
                "guard": guard_info,
                "rival": None,
            }

        first_price = sorted_offers[0]["price"] if sorted_offers else competitors[0]["price"]
        best_competitor_price = competitors[0]["price"]
        step = config.step if config.step > 0 else 10.0

        raw_target_price = config.my_current_price
        action = "HOLD_POSITION"
        expected_pos = 1

        # -------------------------------------------------------------
        # Ветвление по режимам демпинга
        # -------------------------------------------------------------

        if config.mode == "TOP_1":
            # Борьба за 1-е место (демпинг с шагом step)
            expected_pos = 1
            raw_target_price = best_competitor_price - step
            action = "DUMP_TOP_1"

            # Умная подтяжка цены вверх (Margin Maximization), если мы уже на 1 месте
            if my_pos == 1:
                gap = best_competitor_price - config.my_current_price
                if gap > (step * 2):
                    raw_target_price = best_competitor_price - step
                    action = "PULL_UP_PRICE"

        elif config.mode == "MATCH_FIRST":
            # Прижиматься к 1-му месту (-1 ₸ или минимальный шаг)
            expected_pos = 1
            raw_target_price = best_competitor_price - 1.0
            action = "MATCH_FIRST"

            if my_pos == 1:
                gap = best_competitor_price - config.my_current_price
                if gap > 2.0:
                    raw_target_price = best_competitor_price - 1.0
                    action = "PULL_UP_MATCH_FIRST"

        elif config.mode == "TOP_2":
            # Борьба за 2-е место
            expected_pos = 2
            if len(competitors) >= 2:
                second_comp_price = competitors[1]["price"]
                # Встаем чуть ниже 2-го конкурента
                raw_target_price = second_comp_price - step
                # Но не перебиваем 1-го конкурента!
                if raw_target_price <= best_competitor_price:
                    raw_target_price = best_competitor_price + 1.0
            else:
                # Конкурент всего 1 — встаем на шаг выше него (занимаем 2-е место)
                raw_target_price = best_competitor_price + step
            action = "HOLD_TOP_2"

        elif config.mode == "TARGET_POSITION":
            # Занимать N-е место
            target_n = max(1, config.target_position or 1)
            expected_pos = target_n

            if target_n == 1:
                raw_target_price = best_competitor_price - step
                action = "TARGET_POS_1"
            elif len(competitors) >= target_n:
                target_comp_price = competitors[target_n - 1]["price"]
                raw_target_price = target_comp_price - step
                prev_comp_price = competitors[target_n - 2]["price"]
                if raw_target_price <= prev_comp_price:
                    raw_target_price = prev_comp_price + 1.0
                action = f"TARGET_POS_{target_n}"
            else:
                # На карточке меньше конкурентов, чем N
                last_comp_price = competitors[-1]["price"]
                raw_target_price = last_comp_price + step
                action = f"TARGET_POS_{target_n}_AFTER_LAST"

        # -------------------------------------------------------------
        # Защита лимитов Min Price / Max Price и формирование статуса
        # -------------------------------------------------------------

        dumping_status = "WINNING_1ST"
        if config.min_price > 0 and raw_target_price < config.min_price:
            target_price = config.min_price
            action = "REACHED_MIN_PRICE"
            dumping_status = "AT_MIN_PRICE"
        else:
            target_price = raw_target_price
            if config.max_price > 0 and target_price > config.max_price:
                target_price = config.max_price

            if expected_pos == 1:
                dumping_status = "WINNING_1ST"
            elif expected_pos == 2:
                dumping_status = "HOLDING_2ND"
            else:
                dumping_status = "TARGET_POS_REACHED"

        # -------------------------------------------------------------
        # Защита от лесенки: пол под следующим конкурентом, предел за сутки,
        # заморозка. Только поднимает цену, посчитанную выше.
        # -------------------------------------------------------------
        if gcfg.enabled:
            freeze = False
            if ladder_active and gcfg.ladder_response == guard.LADDER_FREEZE:
                freeze = "GUARD_LADDER_FREEZE"
            elif paused:
                freeze = guard.ACTION_DROP_PAUSE
            elif cooling_until and now < cooling_until:
                freeze = guard.HOLD_COOLING
            elif night:
                freeze = guard.HOLD_NIGHT
            guarded, limit_code = guard.apply_guard_limits(
                target_price,
                config.my_current_price,
                guard.floor_reference_prices([c["norm"] for c in competitors], my_norm),
                gcfg,
                config.min_price,
                config.max_price,
                peak_24h=gctx["peak_24h"] if "peak_24h" in gctx else drop.peak,
                freeze=freeze,
            )
            if limit_code:
                target_price = guarded
                action = limit_code
                guard_info["limit"] = limit_code
                if limit_code == guard.ACTION_DAILY_DROP:
                    # Начало паузы: пишется в историю ходов, даже если цена
                    # не изменилась, — от этого момента отсчитывается пауза.
                    guard_info["event"] = guard.ACTION_DAILY_DROP
                    guard_info["alert"] = gcfg.drop_alert
                    guard_info["drop_from"] = drop.peak
                # Мы сознательно не лидируем — честный статус, а не WINNING_1ST.
                if target_price > best_competitor_price:
                    dumping_status = "GUARD_HOLD"

            # Стычка, а мы на 1–2 месте: охлаждаем рынок — цену к началу стычки (днём, до cooling_per_day раз в сутки).
            reset_to = guard.ladder_reset_target(ladder, config.my_current_price, config.max_price)
            if reset_to and guard.ladder_reset_due(ladder, all_moves, gcfg, card_rank, config.max_price, now):
                target_price = reset_to
                action = guard.ACTION_LADDER_RESET
                dumping_status = "GUARD_HOLD"
                guard_info["limit"] = guard.ACTION_LADDER_RESET
                guard_info["event"] = guard.ACTION_LADDER_RESET
                guard_info["alert"] = False

        # Вопрос менеджеру — только если «не учитывать» меняет нашу цену. За сутки
        # наблюдения 04.10.2026 18 из 56 вопросов были о продавцах ниже нашего
        # min_price: ответ на них ничего не менял.
        if asks and not gctx.get("_impact"):
            kept = []
            for a in asks:
                alt = DumpingEngine.calculate_new_price(
                    config, my_merchant_id, sellers_offers, replace(gcfg, observe_only=False),
                    {**gctx, "_impact": True,
                     "decisions": {**decisions, a["merchant_id"]: guard.DECISION_IGNORE}})
                alt_price = float(alt["targetPrice"])
                # Разница в пару тенге — не повод дёргать менеджера (05.10.2026:
                # 6 из 21 вопроса меняли цену на 1–7 ₸).
                if abs(alt_price - float(target_price)) < max(ASK_MIN_IMPACT_TENGE,
                                                               float(target_price) * ASK_MIN_IMPACT_PCT / 100):
                    continue
                kept.append({
                    **a,
                    "price_with": round(float(target_price), 2),
                    "price_without": round(alt_price, 2),
                    "rival_rank": 1 + sum(1 for rp in rival_prices if rp is not None and rp < a["price"]),
                    "our_rank": card_rank,
                    "offers_count": len(rival_prices) + 1,
                })
            guard_info["asks"] = kept

        rival = competitors[0]
        return {
            "targetPrice": round(target_price, 2),
            "currentPos": my_pos or expected_pos,
            "bestCompetitorPrice": best_competitor_price,
            "firstPrice": first_price,
            "action": action,
            "dumping_status": dumping_status,
            "guard": guard_info,
            "rival": {
                **rival.get("norm", {}),
                "merchant_id": rival.get("merchantId"),
                "name": rival.get("merchantName"),
                "price": rival["price"],
            },
        }


SIGNAL_WEAK_RIVAL = "weak_rival"   # вплотную к нам или ниже стоит продавец-провокатор
SIGNAL_LADDER = "ladder"           # лесенка: серия взаимных подрезок
SIGNAL_FARM = "farm"               # на карточке 2+ провокатора
SIGNAL_SHADOW = "shadow"           # один продавец вплотную к нам на 2+ товарах


class BrainWorker:
    """
    2. 🧠 Воркер-Мозг (Pricing Engine / Decision Worker)
    Задача: Расчет новых оптимальных цен на основе правил, лимитов min/max и маржинальности.
    Формирует очередь команд на изменение цен.
    """
    @staticmethod
    def calculate_price_decisions(
        market_snapshot: Dict[str, dict],
        configs: dict,
        merchant_id: str,
        guard_data: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """
        Принимает снимок рынка и возвращает задачи на изменение цен и сводную аналитику.

        guard_data — защита от оттеснения: {"store_defaults": dict,
        "moves_by_sku": {sku: [ходы]}, "decisions_by_sku": {sku: {merchant_id:
        решение менеджера}}, "now": datetime}.
        Без него защита выключена, но сигналы атаки для отчёта всё равно
        считаются (по фиксированным порогам guard.is_suspicious).
        """
        guard_data = guard_data or {}
        now = guard_data.get("now")
        store_guard = guard_data.get("store_defaults") or {}
        moves_by_sku = guard_data.get("moves_by_sku") or {}
        peak_by_sku = guard_data.get("peak_by_sku") or {}
        decisions_by_sku = guard_data.get("decisions_by_sku") or {}
        # Продавцы без рейтинга, о которых надо спросить менеджера; события
        # защиты без изменения цены (начало паузы); тревоги для Телеграма.
        guard_asks = []
        guard_events = []
        guard_alerts = []
        # rival_id -> [(sku, offset)] — для признака «тень» (один продавец
        # держится вплотную к нам на нескольких товарах).
        adjacency: Dict[str, list] = {}
        update_queue = []
        calculation_logs = []
        total_scanned = len(market_snapshot)
        winning_count = 0
        total_saved_margin = 0.0
        # Piggyback: собираем расклад по каждому SKU из уже загруженных offers
        # (никаких лишних запросов к Kaspi) — используется для сводки
        # "война по всем товарам" и лидерборда конкурентов на дашборде.
        price_war_items = []
        competitor_encounters = []

        for sku_key, snap in market_snapshot.items():
            cfg_data = configs.get(sku_key, {})
            p = snap["product_data"]

            price_num = p.get("price_num")
            if price_num is None:
                try:
                    price_str = p.get("price", "0").replace(" ₸", "").replace(" ", "").strip()
                    price_num = float(price_str)
                except Exception:
                    price_num = 0.0

            config = DumpingProductConfig(
                sku=sku_key,
                master_sku=snap.get("master_sku", ""),
                my_current_price=float(price_num),
                min_price=float(cfg_data.get("min_price", 0.0)),
                max_price=float(cfg_data.get("max_price", 0.0)),
                step=float(cfg_data.get("dump_step", 2.0)),
                enabled=bool(cfg_data.get("enabled", True)),
                mode=str(cfg_data.get("mode", "TOP_1")),
                target_position=int(cfg_data.get("target_position", 1)),
                max_delivery_days=cfg_data.get("max_delivery_days"),
                min_seller_rating=cfg_data.get("min_seller_rating"),
                same_city_only=bool(cfg_data.get("same_city_only", False)),
                blacklisted_merchants=cfg_data.get("blacklisted_merchants") or [],
                city_code=str(snap.get("city_code", "750000000"))
            )

            gcfg = guard.resolve_guard_config(store_guard, cfg_data.get("group_guard"), cfg_data.get("guard"))
            res = DumpingEngine.calculate_new_price(
                config, merchant_id, snap.get("offers", []),
                guard_cfg=gcfg,
                guard_ctx={
                    "moves": moves_by_sku.get(sku_key),
                    "decisions": decisions_by_sku.get(sku_key),
                    "now": now,
                    **({"peak_24h": peak_by_sku[sku_key]} if sku_key in peak_by_sku else {}),
                },
            )
            g_res = res.get("guard") or {}
            for a in g_res.get("asks") or []:
                guard_asks.append({
                    "sku": sku_key, "product_name": p.get("name", sku_key), "url": p.get("url"),
                    "our_price": float(config.my_current_price),
                    "merchant_id": a.get("merchant_id"), "merchant_name": a.get("name"),
                    "price": a.get("price"), "reviews": a.get("reviews"),
                    "intercity": a.get("intercity"), "delivery_days": a.get("delivery_days"),
                    "price_with": a.get("price_with"), "price_without": a.get("price_without"),
                    "rival_rank": a.get("rival_rank"), "our_rank": a.get("our_rank"),
                    "offers_count": a.get("offers_count"),
                    "min_price": float(config.min_price or 0) or None,
                })
            if g_res.get("alert") and g_res.get("event") == guard.ACTION_DAILY_DROP:
                guard_alerts.append({
                    "sku": sku_key, "product_name": p.get("name", sku_key),
                    "drop_from": g_res.get("drop_from"), "price": res["targetPrice"],
                    "our_price": float(config.my_current_price),
                })
            new_price = res["targetPrice"]

            # Сохраняем вычисленный статус в объекте товара
            p["dumping_status"] = res["dumping_status"]
            p["dumping_action"] = res["action"]

            if res["dumping_status"] in ("WINNING_1ST", "MAX_PRICE_SINGLE"):
                winning_count += 1

            # --- Piggyback: расклад по этому SKU для сводки войны/лидерборда ---
            raw_offers = snap.get("offers", []) or []
            rival_offers = []
            for o in raw_offers:
                if str(o.get("merchantId", "")) == str(merchant_id):
                    continue
                norm = guard.normalize_offer(o, config.city_code)
                if norm["price"] is None:
                    continue
                norm["name"] = norm["name"] or "Конкурент"
                rival_offers.append(norm)
            rival_offers.sort(key=lambda x: x["price"])
            best_rival = rival_offers[0] if rival_offers else None
            # Место на карточке Kaspi — по всем продавцам, а не по тем, кого
            # учитывает защита: отсеянный провокатор всё равно стоит выше нас.
            card_rank = (
                1 + sum(1 for r in rival_offers if r["price"] < config.my_current_price)
                if rival_offers else res["currentPos"]
            )
            attack = BrainWorker._attack_signals(
                config, rival_offers, res.get("guard") or {}, now
            )
            for r in attack.pop("_adjacent", []):
                adjacency.setdefault(r["merchant_id"], []).append(
                    {"sku": sku_key, "name": r["name"], "offset": r["price"] - config.my_current_price}
                )

            price_war_items.append({
                "sku": sku_key,
                "name": p.get("name", sku_key),
                "url": p.get("url"),
                "our_price": float(config.my_current_price),
                "our_rank": card_rank,
                "competitors_count": len(rival_offers),
                "best_competitor_price": best_rival["price"] if best_rival else None,
                "best_competitor_name": best_rival["name"] if best_rival else None,
                "guard": {
                    **attack,
                    "enabled": (res.get("guard") or {}).get("enabled", False),
                    "ignored": (res.get("guard") or {}).get("ignored", []),
                    "limit": (res.get("guard") or {}).get("limit"),
                    "ladder": (res.get("guard") or {}).get("ladder"),
                    "ladder_active": (res.get("guard") or {}).get("ladder_active", False),
                    "action": res.get("action"),
                    "observe": (res.get("guard") or {}).get("observe", False),
                    "would": (res.get("guard") or {}).get("would"),
                    "asks": len((res.get("guard") or {}).get("asks") or []),
                    "pause_until": (res.get("guard") or {}).get("pause_until"),
                    "last_reset_at": (res.get("guard") or {}).get("last_reset_at"),
                },
            })
            if res["dumping_status"] not in ("WINNING_1ST", "MAX_PRICE_SINGLE") and best_rival:
                competitor_encounters.append({"sku": sku_key, "competitor_name": best_rival["name"]})

            if config.min_price > 0 and new_price > config.min_price:
                total_saved_margin += (new_price - config.min_price)

            # Отправляем обновление цены только при реальном изменении (>= 1.0 ₸)
            if abs(new_price - config.my_current_price) >= 1.0:
                update_queue.append({
                    "sku": sku_key,
                    "old_price": config.my_current_price,
                    "new_price": new_price,
                    "config": config,
                    "calculation_result": res,
                    "product_ref": p
                })
                log_msg = (
                    f"🧠 [Brain] Товар '{p.get('name', sku_key)}' (SKU {sku_key}): "
                    f"расчитано изменение цены {int(config.my_current_price)} ₸ -> {int(new_price)} ₸ "
                    f"(лидер рынка: {res.get('bestCompetitorPrice') or 'Н/Д'} ₸, режим: {config.mode}, действие: {res['action']})."
                )
                calculation_logs.append(log_msg)
            else:
                # Событие защиты без изменения цены (пауза началась, когда мы
                # уже стояли на пределе) — всё равно в историю ходов.
                if g_res.get("event"):
                    guard_events.append({
                        "sku": sku_key, "our_old": config.my_current_price,
                        "our_new": config.my_current_price, "action": g_res["event"],
                        "rival": res.get("rival"),
                    })
                diag_msg = (
                    f"ℹ️ [Brain] Товар '{p.get('name', sku_key)}' (SKU {sku_key}): "
                    f"текущая цена {int(config.my_current_price)} ₸ равна оптимальной {int(new_price)} ₸ "
                    f"(лучший конкурент: {res.get('bestCompetitorPrice') or 'Н/Д'} ₸, действие: {res['action']}, позиция: {res['currentPos']})."
                )
                logger.info(diag_msg)
                calculation_logs.append(diag_msg)

        # «Тень»: один и тот же продавец держится вплотную к нам на 2+ товарах.
        shadows = {rid: hits for rid, hits in adjacency.items() if rid and len({h["sku"] for h in hits}) >= 2}
        for it in price_war_items:
            for rid, hits in shadows.items():
                if any(h["sku"] == it["sku"] for h in hits):
                    g = it["guard"]
                    g.setdefault("signals", [])
                    if SIGNAL_SHADOW not in g["signals"]:
                        g["signals"].append(SIGNAL_SHADOW)
                    g.setdefault("shadow", []).append({
                        "merchant_id": rid,
                        "name": hits[0]["name"],
                        "skus": sorted({h["sku"] for h in hits}),
                    })

        best_price_share = round((winning_count / total_scanned * 100.0), 1) if total_scanned > 0 else 0.0

        analytics = {
            "total_scanned": total_scanned,
            "winning_count": winning_count,
            "best_price_share": best_price_share,
            "total_saved_margin": round(total_saved_margin, 2)
        }

        logger.info(f"🧠 [Brain Worker] Решений: {len(update_queue)}, доля лучшей цены: {best_price_share}%, Сохраненная маржа: {round(total_saved_margin)} ₸")

        return {
            "update_queue": update_queue,
            "analytics": analytics,
            "calculation_logs": calculation_logs,
            "price_war_items": price_war_items,
            "competitor_encounters": competitor_encounters,
            "guard_asks": guard_asks,
            "guard_events": guard_events,
            "guard_alerts": guard_alerts,
        }

    @staticmethod
    def _attack_signals(config: DumpingProductConfig, rivals: List[dict], guard_info: dict, now) -> dict:
        """Признаки атаки по одному товару — для отчёта. Не зависят от того,
        включена ли защита: отчёт должен показать атаку и до её включения."""
        step = config.step if config.step > 0 else 10.0
        ours = config.my_current_price
        # «Вплотную» — ниже нас или выше не больше чем на пару шагов.
        near = max(3.0, step * 2 + 1)
        adjacent = [r for r in rivals if r["price"] <= ours + near]
        signals = []
        suspects = []
        for r in rivals:
            marks = guard.is_suspicious(r, now)
            # Провокатор — нет рейтинга/мало отзывов. «Новый» сам по себе
            # не повод: новичок с сотней отзывов — нормальный продавец.
            if any(m in marks for m in (guard.REASON_NO_RATING, guard.REASON_FEW_REVIEWS)):
                suspects.append({
                    "merchant_id": r["merchant_id"], "name": r["name"], "price": r["price"],
                    "reviews": r.get("reviews"), "rating": r.get("rating"),
                    "intercity": r.get("intercity"),
                    "age_days": guard.merchant_age_days(r["merchant_id"], now),
                    "marks": marks,
                    "adjacent": r in adjacent,
                })
        if any(sp["adjacent"] for sp in suspects):
            signals.append(SIGNAL_WEAK_RIVAL)
        # Ферма — 2+ провокатора вплотную к нам. Раньше считались все на карточке,
        # и 04.10.2026 «под атакой» было 206 SKU из 216: продавцов с <50 отзывами
        # на любой живой карточке несколько.
        if sum(1 for sp in suspects if sp["adjacent"]) >= 2:
            signals.append(SIGNAL_FARM)
        if guard_info.get("ladder"):
            signals.append(SIGNAL_LADDER)
        # Для «тени» — только провокаторы в паре шагов от нас (с обеих сторон).
        # До 05.10.2026 сюда шли все, кто дешевле нас, включая нормальных продавцов
        # своей ниши, и «тень» стояла на 144 SKU из 216.
        suspect_ids = {sp["merchant_id"] for sp in suspects}
        return {
            "signals": signals,
            "suspects": suspects[:8],
            "_adjacent": [r for r in adjacent
                          if r["merchant_id"] in suspect_ids and abs(r["price"] - ours) <= near],
        }
