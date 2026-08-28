"""
Периодическая полная сверка каталога прайс-листом.

Это НЕ второй писатель. Единственный писатель по-прежнему `offer_sync`, и он не
знает про этот модуль ничего: маршрутизации намерений между транспортами нет,
приоритеты не трогаются, `_decide` не меняется. Воркер просто раз в цикл
объявляет Kaspi всё желаемое состояние каталога целиком.

Экономия квоты получается сама собой, без единой строчки про транспорт.
`offer_sync` уже гасит намерение, если желаемое совпало с фактом на Kaspi: фид
применил цену -> BFF её показывает -> JSON молча пропускает запись, не потратив
единицу окна `OFFER`. Разводить намерения по трубам ради этого не нужно, а
каждый лишний режим — это место, где два транспорта разойдутся.

Про режимы. По умолчанию воркер работает в ТЕНЕВОМ режиме: собирает план и
пишет в лог, что он отправил бы, но не отправляет. Чтобы файл поехал, нужны
одновременно `XML_FEED_WORKER=1` (этот воркер) и `XML_FEED_UPLOAD_ENABLED=1`
(общий рубильник отправки), плюс `enabled` в состоянии магазина. Три
независимых замка — потому что цена ошибки здесь измеряется не в неудачном
цикле, а в снятых с продажи карточках.
"""
from typing import Optional
import datetime
import logging

from backend.dumper import feed_state

logger = logging.getLogger(__name__)

# Как часто воркер просыпается. Не равно частоте отправки: большинство
# просыпаний заканчивается «ещё рано» или «нечего везти».
TICK_SECONDS = 300.0

# Минимальная пауза между файлами одного магазина: час.
#
# Не «раз в полчаса, потому что окно FILE тридцатиминутное». Файл жуётся 18-20
# минут, и всё это время он несёт цены, снятые ДО отправки. Слать следующий
# сразу по вердикту — значит держать магазин в состоянии «половина каталога
# объявлена вчерашними значениями» непрерывно. Час даёт между файлами
# сорок минут, когда каталог живёт по JSON и намерения успевают накопиться,
# чтобы файл вёз работу, а не воздух.
MIN_PAUSE_SECONDS = 60 * 60

# Запас поверх времени прошлой обработки. Время разбора растёт с размером
# каталога, и за пределами замеренных ~1100 офферов оно неизвестно, поэтому
# следующий такт считается от факта, а не от константы.
PAUSE_SAFETY = 1.5

# Сколько несрочных намерений оправдывают файл. Ниже этого JSON и дешевле, и
# быстрее: 100 записей — меньше половины окна `OFFER`, они уедут за пару флашей
# и без риска объявлять весь каталог разом.
MIN_INTENTS_FOR_FEED = 100

# Насколько заполненное окно квоты само по себе повод отправить файл, даже если
# намерений мало: если JSON уже упирается, дальше он начнёт откладывать.
QUOTA_PRESSURE_RATIO = 0.6

# Чем считать размер каталога, пока он неизвестен. Заведомо выше порога: на
# первом тике порог не должен молча отсечь магазин, размер которого мы ещё не
# смотрели — иначе он не будет посмотрен никогда.
MIN_OFFERS_UNKNOWN = 10 ** 9


def _shadow() -> bool:
    # Один предикат на воркер и на сторож каталога — см. feed_state.is_shadow.
    return feed_state.is_shadow()


def next_pause_seconds(store_id) -> float:
    """
    Сколько ждать после прошлой отправки. Считается от факта, а не от константы.

    Kaspi проставляет в карточке файла `createdDate` и `endDate` — разница между
    ними и есть настоящее время обработки для КОНКРЕТНОГО размера каталога. У
    Бархана это ~18 минут на 1097 офферов; у магазина втрое больше оно втрое
    больше, и константа там врала бы в опасную сторону.
    """
    state = feed_state.load(store_id)
    processed = state.get("last_processing_seconds")
    if not processed:
        return MIN_PAUSE_SECONDS
    return max(MIN_PAUSE_SECONDS, float(processed) * PAUSE_SAFETY)


def _seconds_since(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return (datetime.datetime.now() - datetime.datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return None


def why_not_now(
    store_id, active_offers: int, pending: int, quota_used_ratio: float,
    *, check_pause: bool = True,
) -> Optional[str]:
    """
    Причина промолчать, или None если пора слать.

    Возвращается именно причина, а не bool: «фид не работает» без объяснения
    превращается в расследование по логам, а тут каждый отказ должен сам себя
    называть.
    """
    blocked = feed_state.eligible(store_id, active_offers)
    if blocked:
        return blocked

    # Пауза считается от ПОЗДНЕЙШЕГО из двух событий: разобранного файла и
    # собранного плана. Только `settled_at` тут мало: в теневом режиме файл не
    # отправляется, `settled_at` не проставляется никогда, и воркер выкачивал
    # весь каталог каждые пять минут. Замерено на Бархане 2026-08-26 — две
    # полные выкачки за шесть минут (15:15 и 15:21). Это ровно тот паттерн
    # лишнего трафика, за который IP этого VPS уже банили (HANDOFF 3.8/3.9),
    # и в тени он опаснее, чем в бою: в бою его гасил `settled_at`.
    state = feed_state.load(store_id)
    ages = [
        age
        for age in (
            _seconds_since(state.get("settled_at")),
            _seconds_since(state.get("last_plan_at")),
        )
        if age is not None
    ]
    since = min(ages) if ages else None
    pause = next_pause_seconds(store_id)
    if check_pause and since is not None and since < pause:
        return "с прошлой отправки прошло {:.0f} мин из {:.0f}".format(since / 60, pause / 60)

    if pending < MIN_INTENTS_FOR_FEED and quota_used_ratio < QUOTA_PRESSURE_RATIO:
        return (
            "везти нечего: {} намерений (порог {}), окно квоты занято на {:.0%}".format(
                pending, MIN_INTENTS_FOR_FEED, quota_used_ratio
            )
        )
    return None


def run_for_store(store, on_flight=None) -> dict:
    """
    Один проход по одному магазину. Синхронный — вызывается через to_thread.

    Порядок шагов важен, и он не тот, что кажется. Сначала — дешёвые проверки
    по БД и файлу состояния, и только потом сборка плана.

    Сборка «бесплатна» лишь по квоте Kaspi: она выкачивает ВЕСЬ каталог через
    bff/offer-view/list, а это полтораста страниц у крупного магазина. Делать
    это на каждом тике по каждому магазину — ровно тот паттерн постоянного
    лишнего трафика, за который IP этого VPS уже банили (HANDOFF 3.8/3.9).
    Поэтому выкачка происходит, только когда мы уже намерены отправлять.

    Число активных офферов для порога берётся из прошлой сборки (оно живёт в
    состоянии магазина). На самом первом тике его нет — тогда план собирается
    один раз, и дальше порог проверяется даром.
    """
    from backend.dumper.feed_plan import build_feed_plan, render_plan_report
    from backend.services.kaspi_quota import quota
    from backend.services.offer_sync import pending_intents

    result = {"store_id": store.store_id, "sent": False, "reason": None}

    known_active = feed_state.load(store.store_id).get("active_offers")
    pending = len(pending_intents(store.store_id))
    state = quota.get(store.store_id, str(store.merchant_id))
    used_ratio = (state.used / state.limit) if getattr(state, "limit", 0) else 0.0

    # Порог по размеру каталога проверяем, только если размер уже известен:
    # иначе первый тик обязан собрать план, чтобы его узнать.
    reason = why_not_now(
        store.store_id,
        known_active if known_active is not None else MIN_OFFERS_UNKNOWN,
        pending,
        used_ratio,
    )
    if reason:
        result["reason"] = reason
        return result

    plan = build_feed_plan(store.token, store.store_id, use_accepted_identity=True)
    active = plan.total
    # Отметка ставится СРАЗУ после сборки, а не после отправки: дорогая здесь
    # именно выкачка каталога, и она уже случилась — независимо от того, чем
    # кончится проход (тень, блокеры, неудачная отправка).
    feed_state.save(
        store.store_id,
        active_offers=active,
        last_plan_at=datetime.datetime.now().isoformat(),
    )

    if plan.blockers:
        # Артикулы с невидимыми символами. Файл на них сослаться не может, и
        # Kaspi погасит эти карточки — тот же случай, что 2026-08-24.
        result["reason"] = "битые артикулы: {}".format(plan.blockers[:5])
        result["blockers"] = list(plan.blockers)

        if _shadow():
            # В тени рубильник НЕ дёргаем. `disable()` несимметричен намеренно
            # (см. докстринг feed_state): он берёт полчаса без фида в обмен на
            # ночь ручного восстановления каталога. В теневом режиме второй
            # половины сделки не существует — отправить нечего, потерять
            # карточки нечем, — а первая обходится дороже, чем кажется:
            # `eligible()` начинает отбивать проход ДО сборки плана, и мы
            # теряем ровно те наблюдения, ради которых тень и включена.
            # Замерено 2026-08-27: блокеры в 06:09 выключили теневой прогон на
            # 12+ часов (0 тиков), при том что отправка была невозможна.
            feed_state.save(
                store.store_id,
                last_blockers=result["blockers"][:5],
                last_blockers_at=datetime.datetime.now().isoformat(),
            )
            logger.warning(
                "👁 [FeedWorker] Стор %s: СОБРАЛ БЫ файл, НО не отправил бы — %s. "
                "Тень: фид оставлен включённым, рубильник не тронут.",
                store.store_id, result["reason"],
            )
            return result

        feed_state.disable(store.store_id, result["reason"])
        result["disabled"] = result["reason"]
        return result

    # Второй заход: теперь размер каталога известен точно, а не по памяти.
    # Паузу тут НЕ проверяем: дешёвый заход её уже проверил секунду назад, а
    # `last_plan_at` мы только что проставили сами — повторная проверка
    # отвергла бы собственный же проход, ничего при этом не сэкономив.
    reason = why_not_now(store.store_id, active, pending, used_ratio, check_pause=False)
    if reason:
        result["reason"] = reason
        return result

    if _shadow():
        result["reason"] = "теневой режим"
        logger.info(
            "👁 [FeedWorker] Стор %s: ОТПРАВИЛ БЫ файл — офферов %s, изменений %s.\n%s",
            store.store_id, plan.total, plan.changed_offers, render_plan_report(plan, limit=10),
        )
        return result

    from backend.dumper.feed_uploader import send_plan

    feed_state.mark_sent(store.store_id, "pending", plan.snapshot.taken_at)
    window = feed_state.blind_window(store.store_id)
    if window:
        # Единственная защита от потери чужой правки — предупреждение. Ловушки
        # нет: файл объявляет состояние на момент снимка, и правку, сделанную
        # в кабинете за эти минуты, он откатит, а сверка этого не увидит —
        # объявленное и фактическое после отката совпадут.
        result["blind_until"] = window["until"].isoformat()
        # Колбэк, а не await: эта функция синхронная и висит в потоке до конца
        # полёта, а предупреждение обязано уйти ДО него, иначе оно бесполезно.
        if on_flight is not None:
            try:
                on_flight(store.store_id, result["blind_until"])
            except Exception as err:
                logger.error("[FeedWorker] Не удалось объявить окно слепоты: %r", err)
        logger.warning(
            "🕶 [FeedWorker] Стор %s: ОКНО СЛЕПОТЫ %s → ~%s. Правки в кабинете в эти "
            "минуты будут откачены файлом.",
            store.store_id,
            plan.snapshot.taken_at.strftime("%H:%M:%S"),
            window["until"].strftime("%H:%M:%S"),
        )
    try:
        verdict = send_plan(
            plan, store.store_id, str(store.merchant_id),
            confirm=str(store.merchant_id), allow_changes=True,
        )
    except Exception as err:
        # Полёт закрываем в любом случае: иначе один неудачный запуск запер бы
        # магазин навсегда — `in_flight` блокирует следующую отправку.
        feed_state.save(store.store_id, in_flight=None)
        result["reason"] = "отправка не удалась: {!r}".format(err)
        feed_state.disable(store.store_id, result["reason"])
        result["disabled"] = result["reason"]
        return result

    record_processing_time(store.store_id, verdict)
    result["sent"] = True
    result["verdict"] = verdict.summary()

    # Вердикт говорит «файл разобран», а не «каталог в порядке». Что именно
    # произошло с карточками, показывает только сверка со свежим снимком.
    if verdict.archive_path:
        import pathlib

        from backend.dumper.feed_settle import settle_file

        result["report"] = settle_file(
            store.store_id, pathlib.Path(verdict.archive_path), verdict, token=store.token,
        )
    else:
        # Архив не записался — сверять не с чем. Это не повод считать, что всё
        # хорошо: мы только что объявили весь каталог и не можем проверить исход.
        result["disabled"] = "файл отправлен, но не сохранён — сверить исход невозможно"
        feed_state.disable(store.store_id, result["disabled"])
    return result


async def notify_blind_window(store_id, until_iso: str) -> None:
    """
    Сказать владельцу, что ближайшие минуты каталог трогать не стоит.

    Не urgent: это не авария, а предупреждение по расписанию, и будить им
    человека ночью незачем — ночью он в кабинете и не сидит. Дедуп по времени
    закрытия окна, чтобы повтор одного и того же полёта не пришёл дважды.
    """
    import datetime as _dt

    from backend.chatbot import notify_dispatch

    try:
        until = _dt.datetime.fromisoformat(until_iso)
    except (TypeError, ValueError):
        return

    text = "\n".join([
        "📤 *Прайс-лист ушёл в Kaspi*",
        "",
        "Применится примерно к *{}*.".format(until.strftime("%H:%M")),
        "",
        "Если в эти минуты поменять цену или остаток в кабинете — файл вернёт "
        "прежнее значение, и мы этого не увидим. Новый товар, созданный сейчас, "
        "может уехать в архив.",
        "",
        "Несколько минут — и правьте спокойно.",
    ])
    try:
        await notify_dispatch.dispatch(
            str(store_id), "problems", text,
            dedup_key="feed-flight-{}-{}".format(store_id, until_iso),
        )
    except Exception as err:
        logger.error("[FeedWorker] Предупреждение об окне слепоты не ушло: %r", err)


async def notify_feed_disabled(store_id, reason: str) -> None:
    """
    Разбудить человека, когда фид выключил сам себя.

    До 2026-08-27 `disable()` умел только `logger.error`, и выключение было
    невидимым: фид простоял выключенным 12 часов, а узналось об этом
    раскопками в логах по другому поводу. Лог для этого не годится — его никто
    не читает, и это ровно тот вывод, который уже сделан в `notify_settle_report`.

    Фид обратно включает только человек, поэтому сообщение обязано сказать не
    только «выключен», но и «сам не включится».
    """
    from backend.chatbot import notify_dispatch

    text = "\n".join([
        "🚨 *ПРАЙС-ЛИСТ ВЫКЛЮЧЕН*",
        "",
        "Причина: {}".format(reason),
        "",
        "Сам он обратно не включится — нужно починить причину и включить руками.",
    ])
    try:
        await notify_dispatch.dispatch(
            str(store_id), "problems", text,
            urgent=True, dedup_key="feed-disabled-{}".format(store_id),
        )
    except Exception as err:
        logger.error("[FeedWorker] Тревога о выключении фида не ушла в Телеграм: %r", err)


async def run_feed_cycle() -> dict:
    """Один проход по всем активным магазинам. Вынесен ради юнит-тестов."""
    import asyncio

    from backend.security.store_context import list_active_stores

    totals = {"checked": 0, "sent": 0}
    for store in await list_active_stores():
        totals["checked"] += 1
        try:
            loop = asyncio.get_running_loop()

            def announce(store_id, until_iso):
                # Из чужого потока в цикл событий: run_for_store синхронный, а
                # dispatch — корутина. Результат намеренно не ждём: рассылка не
                # должна задерживать отправку файла.
                asyncio.run_coroutine_threadsafe(
                    notify_blind_window(store_id, until_iso), loop
                )

            result = await asyncio.to_thread(run_for_store, store, announce)
            if result["sent"]:
                totals["sent"] += 1
                report = result.get("report")
                if report is not None:
                    from backend.dumper.feed_settle import notify_settle_report

                    await notify_settle_report(store.store_id, report)
            elif result["reason"]:
                logger.debug("[FeedWorker] Стор %s: %s", store.store_id, result["reason"])

            # Отдельно от sent/not-sent: выключиться фид может и после удачной
            # отправки (архив не записался), и вместо неё.
            if result.get("disabled"):
                await notify_feed_disabled(store.store_id, result["disabled"])
        except Exception as err:
            logger.error("[FeedWorker] Стор %s: цикл упал: %r", store.store_id, err)
    return totals


async def feed_worker_loop(interval_seconds: float = TICK_SECONDS):
    import asyncio

    logger.info(
        "🟢 [FeedWorker] Полная сверка прайс-листом запущена (тик раз в %.0fс, режим: %s).",
        interval_seconds, "ТЕНЕВОЙ" if _shadow() else "БОЕВОЙ",
    )
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await run_feed_cycle()
        except asyncio.CancelledError:
            logger.info("⏹ [FeedWorker] Остановлен.")
            break
        except Exception as err:
            logger.error("❌ [FeedWorker] Ошибка цикла: %r", err)


def record_processing_time(store_id, verdict) -> None:
    """Запомнить, сколько Kaspi жевала файл — этим считается следующий такт."""
    try:
        started = datetime.datetime.fromisoformat(verdict.created)
        ended = datetime.datetime.fromisoformat(verdict.ended)
    except (TypeError, ValueError):
        return
    seconds = (ended - started).total_seconds()
    if seconds > 0:
        feed_state.save(store_id, last_processing_seconds=seconds)
