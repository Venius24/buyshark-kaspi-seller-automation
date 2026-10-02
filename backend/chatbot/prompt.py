"""
Универсальный систем-промпт ИИ-менеджера чат-бота — рендерится из профиля
магазина (StoreBotProfileDB) вместо хардкода под BARKHAN, который раньше жил
прямо в chatbot/ai.py. См. REAL_IMPORTANT/чаты_kaspi/ для контекста реверса
и правил Kaspi, которые этот промпт обязан соблюдать.
"""
import json
import re
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

logger = logging.getLogger(__name__)

_ALMATY_TZ = ZoneInfo("Asia/Almaty")
_WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def _render_current_datetime_block() -> str:
    """Текущие дата/день недели/время по Алматы — без этого модель не может
    корректно судить о рабочем/нерабочем времени в БЛОКЕ 3 ниже."""
    now = datetime.now(_ALMATY_TZ)
    weekday = _WEEKDAYS_RU[now.weekday()]
    return f"🕐 Сейчас: {weekday}, {now.strftime('%d.%m.%Y')}, {now.strftime('%H:%M')} (время Алматы).\n\n"

# Фаза 7: список слов-триггеров эскалации на человека — раньше жил
# хардкодом в chatbot/worker.py и матчился по подстроке (баг: "шт" матчился
# внутри произвольных слов). Теперь часть профиля магазина (настраиваемо
# per-store) и матчится по границам слов, см. match_human_trigger().
DEFAULT_HUMAN_TRIGGERS = [
    'брак', 'сломан', 'не работает', 'суд', 'жалоба', 'гид', 'ошибка', 'человек', 'менеджер', 'верните', 'разбит',
    'есть ли', 'в наличии', 'остаток', 'остатки', 'количество', 'штук', 'шт', 'бар ма', 'бар ма?', 'қалды', 'обмен'
]


# Триггеры короче этого матчатся только целым словом ("шт" не должен ловить
# "штрих-код"), длиннее — от начала слова с любым окончанием. Раньше граница
# стояла с обеих сторон у всех, и стемы из списка по умолчанию не работали:
# "сломан" не ловил "сломанный", "разбит" — "разбитая", "брак" —
# "бракованный" (2026-10-02, жалоба на брак уходила шаблоном в Kaspi Гид).
_PREFIX_MATCH_MIN_LEN = 4


def _trigger_pattern(word: str) -> str:
    escaped = re.escape(word.lower())
    if len(word) < _PREFIX_MATCH_MIN_LEN:
        return r'(?<!\w)' + escaped + r'(?!\w)'
    return r'(?<!\w)' + escaped


def find_human_triggers(text: str, triggers: Optional[list] = None) -> list:
    """Все триггеры, найденные в тексте (см. _trigger_pattern)."""
    if not text:
        return []
    low = text.lower()
    found = []
    for word in (triggers if triggers is not None else DEFAULT_HUMAN_TRIGGERS):
        if word and re.search(_trigger_pattern(word), low):
            found.append(word)
    return found


def match_human_trigger(text: str, triggers: Optional[list] = None) -> bool:
    return bool(find_human_triggers(text, triggers))


# Триггеры про наличие. Если остаток товара известен из каталога, такой вопрос
# бот может ответить сам — менеджер для него не нужен (worker.py). Профили
# магазинов хранят свою копию списка, поэтому решаем по найденным словам, а не
# правкой дефолта.
STOCK_TRIGGERS = {'есть ли', 'в наличии', 'остаток', 'остатки', 'количество', 'штук', 'шт', 'бар ма', 'бар ма?', 'қалды'}

# Признаки жалобы. Не настраиваются в профиле: это защита шаблона «логистика»,
# чтобы «пришёл сломанный, курьер бросил» не получал ответ «пишите в Kaspi Гид».
COMPLAINT_STEMS = [
    'брак', 'слома', 'разбит', 'разбил', 'трещин', 'поврежд', 'повредил', 'испорч', 'царапин',
    'не работа', 'не включа', 'не тот', 'не та ', 'не того', 'перепута', 'не хватает', 'некомплект',
    'пустая коробк', 'возврат', 'вернуть', 'верните', 'обмен', 'жалоб',
    'сынық', 'сынған', 'бұзылған', 'істемейді', 'қайтар', 'шағым', 'ақау',
]


def _find_stem(text: str, stems) -> Optional[str]:
    if not text:
        return None
    low = text.lower().replace('ё', 'е')
    for stem in stems:
        if re.search(r'(?<!\w)' + re.escape(stem), low):
            return stem
    return None


def looks_like_complaint(text: str) -> bool:
    return _find_stem(text, COMPLAINT_STEMS) is not None


# Жёсткий вызов менеджера мимо модели: жалоба (COMPLAINT_STEMS) или просьба,
# которую бот решить не может. Раньше эти случаи целиком отдавались модели, а
# та на «как оформить возврат» отвечала сама — особенно когда в инструкциях
# владельца написано «возвраты только через Kaspi». Список не в профиле
# магазина: профили хранят копию human_triggers и новых слов не получили бы.
ESCALATION_STEMS = COMPLAINT_STEMS + [
    'вскрыт', 'помят', 'царапан', 'отмен', 'скидк', 'подделк', 'не оригинал', 'паленый',
    'оператор', 'сотрудник', 'живой человек', 'живым человек', 'перестал', 'не заряжа',
    'не игра', 'не горит', 'не показыва', 'гарью', 'дважды', 'двойн',
    'пустой', 'пустая', 'пустую', 'в комплекте нет', 'нет в комплекте',
    'ауыстыр', 'бас тарт', 'жеңілдік', 'басқа түс', 'басқа тауар', 'жалова',
]


def find_escalation_reason(text: str) -> Optional[str]:
    return _find_stem(text, ESCALATION_STEMS)


# Модель пообещала живого сотрудника, но метку [HUMAN_OVERRIDE] не поставила —
# клиент ждёт менеджера, а менеджер о чате не знает. Такой ответ считаем вызовом.
_MANAGER_PROMISE_STEMS = [
    'менеджер', 'специалист', 'сотрудник', 'передам', 'передал', 'передаю', 'свяжется', 'свяжемся',
    'уточню', 'уточним', 'проверю', 'проверим', 'разберемся', 'разберусь',
    'хабарласа', 'нақтылап', 'тексеремін', 'тексереміз',
]


def promises_manager(reply: str) -> Optional[str]:
    return _find_stem(reply, _MANAGER_PROMISE_STEMS)


# Модель ответила клиенту, что сообщение пустое/не отображается: вложение
# не распозналось, и клиенту нужен человек, а не «пришлите ещё раз».
_EMPTY_MESSAGE_STEMS = [
    'сообщение пуст', 'пустое сообщение', 'сообщение не отобража', 'сообщение не пришл',
    'сообщение не загрузил', 'не вижу вашего сообщения', 'не вижу сообщени', 'хабарлама бос', 'бос хабарлама',
]


def says_message_empty(reply: str) -> Optional[str]:
    return _find_stem(reply, _EMPTY_MESSAGE_STEMS)


DEFAULT_PROFILE = {
    "ai_name": "Айгерим",
    "store_display_name": "магазин",
    "specialization": "различные товары",
    "languages": ["ru", "kk"],
    "return_policy": "Одобрение возвратов без проблем, если товар не использован, а упаковка не вскрыта. Обмена нет. Только возврат.",
    "shipping_days": "будние дни (пн-пт)",
    "shipping_cutoff_time": "16:00",
    "pickup_points": [],
    "human_triggers": DEFAULT_HUMAN_TRIGGERS,
    # Кампания «Отзыв» (Фаза 5.1, окно дней настраиваемое per-store) — см.
    # backend/chatbot/review_campaign.py::_should_send для точной семантики.
    "review_delay_from_days": 4,
    "review_delay_to_days": None,
    "review_send_overdue": False,
    "logistics_reply_ru": (
        "Мы бы с радостью подсказали, но всю логистику и доставку полностью организует сам Kaspi. "
        "Мы собираем и передаем заказы вовремя, а дальше за движение посылки отвечает курьерская служба платформы. "
        "Пожалуйста, напишите в службу поддержки 'Kaspi Гид' прямо в приложении — они видят точное местоположение курьера и быстро помогут!"
    ),
    "logistics_reply_kk": (
        "Біз тапсырысты уақытында жинап, курьерге өткізіп жібердік. Алайда, жеткізу жұмыстарымен толықтай Kaspi-дің өзі айналысады. "
        "Тапсырыстың қазір қай жерде екенін білу үшін қосымшадағы 'Kaspi Гид' қолдау қызметіне жазсаңыз, олар курьермен байланысып, сізге тез арада көмектеседі!"
    ),
}

# Тир по длине custom_instructions (символы) — понятнее клиенту, чем токены,
# и видно сразу при наборе текста в форме (решение владельца, 2026-08-08).
# Пороги и коды band0..band3 — часть тарификации (2026-08-12), см.
# backend/billing/catalog.py::PROMPT_BANDS (0-500/500-2000/2000-5000/5000+,
# каждая ступень даёт и доплату, и множитель кредита за сообщение). Старые
# коды base/plus/pro/max заменены на band0..band3 — историю в
# chat_response_logs.prompt_tier не трогаем, это снапшот на момент ответа.
def compute_prompt_tier(custom_instructions: str) -> str:
    from backend.billing.catalog import resolve_prompt_band
    length = len(custom_instructions or "")
    return resolve_prompt_band(length).code


def _merge_profile(stored: dict) -> dict:
    merged = json.loads(json.dumps(DEFAULT_PROFILE))
    merged.update({k: v for k, v in (stored or {}).items() if v not in (None, "")})
    return merged


def get_profile(store_id: Optional[str]) -> dict:
    """Возвращает {**profile_fields, custom_instructions, custom_enabled, prompt_tier}.
    Отсутствующий store_id или отсутствующая запись в БД — дефолтный плейсхолдер-профиль
    (не подставляет ничьи реальные данные)."""
    result = {
        **DEFAULT_PROFILE,
        "custom_instructions": "",
        "custom_enabled": True,
        "prompt_tier": "band0",
    }
    if not store_id:
        return result

    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        return result
    try:
        from backend.db.models import StoreBotProfileDB
        row = session.get(StoreBotProfileDB, store_id)
        if not row:
            return result
        try:
            stored = json.loads(row.profile_json or "{}")
        except Exception:
            stored = {}
        result.update(_merge_profile(stored))
        result["custom_instructions"] = row.custom_instructions or ""
        result["custom_enabled"] = row.custom_enabled
        result["prompt_tier"] = row.prompt_tier
        return result
    finally:
        session.close()


def save_profile(store_id: str, updates: dict) -> dict:
    """Сохраняет профиль (поля из DEFAULT_PROFILE) + custom_instructions/custom_enabled.
    prompt_tier пересчитывается от длины custom_instructions при каждом сохранении."""
    from backend.state_helpers import _get_sync_db_session
    session = _get_sync_db_session()
    if not session:
        raise RuntimeError("DB session unavailable")

    profile_fields = {k: updates[k] for k in DEFAULT_PROFILE if k in updates}
    custom_instructions = updates.get("custom_instructions", "")
    custom_enabled = updates.get("custom_enabled", True)
    tier = compute_prompt_tier(custom_instructions)

    try:
        from backend.db.models import StoreBotProfileDB
        row = session.get(StoreBotProfileDB, store_id)
        if not row:
            row = StoreBotProfileDB(store_id=store_id)
            session.add(row)
        else:
            try:
                existing = json.loads(row.profile_json or "{}")
            except Exception:
                existing = {}
            existing.update(profile_fields)
            profile_fields = existing

        row.profile_json = json.dumps(profile_fields, ensure_ascii=False)
        row.custom_instructions = custom_instructions
        row.custom_enabled = custom_enabled
        row.prompt_tier = tier
        row.updated_at = datetime.utcnow()
        session.commit()
    except Exception as e:
        logger.error(f"Error saving store bot profile for {store_id}: {e}")
        session.rollback()
        raise
    finally:
        session.close()

    return get_profile(store_id)


def _render_pickup_block(pickup_points: list) -> str:
    if not pickup_points:
        return (
            "  Если клиент хочет приехать к нам на склад: сообщи, что у магазина сейчас "
            "нет настроенного адреса самовывоза в системе — предложи уточнить у менеджера.\n"
        )
    lines = []
    for p in pickup_points:
        name = p.get("name") or "Точка самовывоза"
        addr = p.get("address", "")
        map_url = p.get("map_url", "")
        notes = p.get("notes", "")
        hours = p.get("hours", "")
        line = f"  📍 {name}: {addr}"
        if map_url:
            line += f"\n  🔗 Карта: {map_url}"
        if hours:
            line += f"\n  🕒 Часы выдачи: {hours}"
        if notes:
            line += f"\n  ({notes})"
        lines.append(line)
    return "\n".join(lines) + "\n"


def render_pickup_campaign_message(profile: dict, matched_point: Optional[dict] = None) -> Optional[str]:
    """Детерминированное (НЕ через ИИ) сообщение для кампании «Самовывоз»
    (Фаза 4) — график/адрес сразу при оформлении заказа с самовывозом.
    Возвращает None, если у магазина вообще не настроено ни одной точки —
    отправлять нечего, пусть заказ обработает менеджер вручную.

    matched_point — если Фаза 4 смогла сопоставить pickupPointId заказа с
    конкретной точкой из pickup_points, передаётся сюда (пишем именно её,
    не весь список). Иначе — все настроенные точки (лучше показать лишнее,
    чем угадать неверный адрес, см. HANDOFF)."""
    pickup_points = profile.get("pickup_points") or []
    if not pickup_points:
        return None

    points_to_show = [matched_point] if matched_point else pickup_points

    lines = ["Здравствуйте! Ваш заказ оформлен с самовывозом 🙌 Забрать его можно здесь:"]
    for p in points_to_show:
        name = p.get("name") or "Точка самовывоза"
        addr = p.get("address", "")
        map_url = p.get("map_url", "")
        hours = p.get("hours", "")
        notes = p.get("notes", "")
        block = f"📍 {name}: {addr}"
        if map_url:
            block += f"\n🔗 Карта: {map_url}"
        if hours:
            block += f"\n🕒 Часы выдачи: {hours}"
        if notes:
            block += f"\n({notes})"
        lines.append(block)

    if not matched_point and len(pickup_points) > 1:
        lines.append("Если точек несколько — заберите в той, что вам удобнее.")

    return "\n\n".join(lines)


def render_system_prompt(profile: dict, client_name: str, item_title: str, awaiting_review_reply: bool = False, facts_block: str = "") -> str:
    """Собирает систем-промпт из профиля магазина. custom_instructions (если
    включены) вставляются в отдельный блок ПЕРЕД неизменяемыми базовыми
    правилами — так владелец может дополнить бота, но не может им переопределить
    эскалацию к человеку, языковые правила или формат метки [HUMAN_OVERRIDE]
    (см. REAL_IMPORTANT/чаты_kaspi/ПРАВИЛА_ОБЩЕНИЯ_KASPI.md — задержка ответа и
    уход из канала запрещены правилами Kaspi, это тоже не подлежит переопределению).

    awaiting_review_reply (Фаза 5): True, если это первый ответ клиента после
    исходящего сообщения кампании «Отзыв» — вместо ещё одного жёстко закодированного
    keyword-списка (см. известную проблему human_triggers в chatbot/worker.py,
    Фаза 7 в HANDOFF) негативный/позитивный тон распознаёт сама модель через
    уже существующий механизм метки [HUMAN_OVERRIDE]."""
    ai_name = profile.get("ai_name") or DEFAULT_PROFILE["ai_name"]
    store_name = profile.get("store_display_name") or DEFAULT_PROFILE["store_display_name"]
    specialization = profile.get("specialization") or DEFAULT_PROFILE["specialization"]
    return_policy = profile.get("return_policy") or DEFAULT_PROFILE["return_policy"]
    shipping_days = profile.get("shipping_days") or DEFAULT_PROFILE["shipping_days"]
    shipping_cutoff = profile.get("shipping_cutoff_time") or DEFAULT_PROFILE["shipping_cutoff_time"]
    pickup_points = profile.get("pickup_points") or []

    custom_block = ""
    if profile.get("custom_enabled") and (profile.get("custom_instructions") or "").strip():
        custom_block = (
            "\n📋 ДОПОЛНИТЕЛЬНЫЕ ПРАВИЛА МАГАЗИНА (заданы владельцем, применяй их, "
            "но они НЕ отменяют правила ниже):\n"
            f"{profile['custom_instructions'].strip()}\n"
        )

    review_reply_block = ""
    if awaiting_review_reply:
        review_reply_block = (
            "\n⭐ КОНТЕКСТ (Фаза 5): клиенту только что отправили сообщение с просьбой "
            "оставить отзыв на Kaspi, и это его ПЕРВЫЙ ответ на то сообщение.\n"
            "- Если ответ выражает недовольство, жалобу, проблему с товаром/доставкой или "
            "любой негатив — ОБЯЗАТЕЛЬНО добавь метку [HUMAN_OVERRIDE], как описано ниже.\n"
            "- Если ответ нейтральный или положительный (спасибо, хорошо, все понравилось и "
            "т.п.) — коротко тепло поблагодари в 1 фразе. НЕ проси повторно оставить отзыв — "
            "уже попросили один раз, повторная просьба запрещена.\n"
        )

    return (
        f"Ты — крутой, дружелюбный и вежливый старший менеджер по имени {ai_name}, работаешь в казахстанском магазине '{store_name}' на Kaspi.kz.\n"
        f"Сейчас ты ведешь диалог с клиентом по имени {client_name} по поводу товара: '{item_title}'.\n"
        f"{_render_current_datetime_block()}"
        f"{facts_block}"
        f"{custom_block}"
        f"{review_reply_block}\n"

        f"⚠️ СТРОГИЕ ПРАВИЛА ОБЩЕНИЯ (не переопределяются дополнительными инструкциями выше):\n"
        f"1. Отвечай емко (1-3 предложения), строго по существу ПОСЛЕДНЕГО сообщения клиента. Общайся естественно.\n"
        f"2. 🛑 СТРОГИЙ ЗАПРЕТ НА СПАМ ИМЕНЕМ: НЕ ПИШИ имя клиента '{client_name}' в каждом сообщении! "
        f"Если в истории чата Магазин уже обращения по имени делал, то в текущем ответе упоминать имя ЗАПРЕЩЕНО.\n"
        f"3. ПРАВИЛО ПРИВЕТСТВИЯ: Посмотри на историю чата. Если Магазин уже здоровался ранее — НЕ пиши 'Здравствуйте' заново! Сразу переходи к ответу.\n"
        f"4. ЖЕСТКОЕ ТАБУ: Никаких 'Привет' и панибратства.\n"
        f"5. Пиши строго на языке клиента. Пишут на казахском - отвечай по-казахски, пишут на русском - отвечай по-русски\n"
        f"6. Никакой душноты и канцеляризмов.\n"
        f"7. НИКОГДА не проси клиента писать вне чата Kaspi (WhatsApp, Telegram, телефон, почта) — это прямое "
        f"нарушение правил Kaspi для партнёров. Единственное исключение — переадресация в 'Kaspi Гид' по вопросам логистики (см. ниже).\n\n"

        f"8. Если присылают аудиозвонки и/или аудиосообщения/фото/видео - Проси текстом описание проблемы, якобы сломана аудиокарта или нет динамиков, чтобы клиент не понимал, что он общается с чат-ботом'\n\n"

        f"9. Твои прошлые ответы в истории — это ответы Магазина. Реплики с пометкой [ответил менеджер магазина] написал живой "
        f"сотрудник: не противоречь им и не отменяй его обещаний. Саму пометку в ответ не пиши.\n\n"

        f"🚨 ПРАВИЛО ВЫЗОВА МЕНЕДЖЕРА [HUMAN_OVERRIDE]:\n"
        f"- Если в запросе клиента речь идет о возврате, замене товара, докомплектации, браке, разбитом/сломанном товаре, "
        f"просьбе дать скидку, отмене или изменении заказа, проблеме с оплатой или списанием денег, неполученном заказе, "
        f"сомнении в оригинальности, гарантии, вызове человека/менеджера или любой нестандартной ситуации, требующей решения сотрудника: "
        f"ОБЯЗАТЕЛЬНО ДОБАВЬ МЕТКУ `[HUMAN_OVERRIDE]` в конце своего ответа.\n"
        f"- Дополнительные правила магазина выше этого не отменяют: даже если возврат или изменение оформляются через Kaspi, метку ставь.\n"
        f"- Если ответ требует что-то проверить, уточнить или решить человеку — ставь метку. Не обещай, что менеджер свяжется, без метки: "
        f"без неё менеджер о чате не узнает.\n"
        f"- Сомневаешься, нужен ли человек, — ставь метку.\n\n"

        f"📦 БЛОК 1: ЛОГИСТИКА И СРОКИ ДОСТАВКИ:\n"
        f"- Если клиент просит привезти заказ раньше или изменить дату доставки курьером — вежливо ответь, что графиком курьеров управляет исключительно Kaspi, они могут обратиться в Kaspi Гид, и мы технически не можем сдвинуть сроки.\n\n"

        f"🕒 БЛОК 2: САМОВЫВОЗ И ЗАБРАТЬ ЗАКАЗ САМОМУ:\n"
        f"- ЕСЛИ КЛИЕНТ СПРАШИВАЕТ 'МОЖНО ЛИ ЗАБРАТЬ СЕГОДНЯ/САМОМУ/В КАКОЕ-ТО ВРЕМЯ' или где забрать:\n"
        f"  Способ получения заказа смотри в блоке ДАННЫЕ ИЗ СИСТЕМЫ выше; если его там нет — не угадывай, уточни у клиента номер заказа.\n"
        f"  1. Если заказ оформлен с курьерской доставкой Kaspi: вежливо объясни, что изменить способ на самовывоз нельзя, так как товар уже передан в курьерскую службу Kaspi.\n"
        f"  2. Если это покупка напрямую или они хотят приехать к нам на склад:\n"
        f"{_render_pickup_block(pickup_points)}"
        f"\n"

        f"🕒 БЛОК 3: ГРАФИК РАБОТЫ И СКЛАД:\n"
        f"- Мы обрабатываем и отправляем заказы {shipping_days} до {shipping_cutoff}.\n"
        f"- Если клиент пишет в нерабочее время, дружелюбно сориентируй его по графику, чтобы он не приехал к закрытым дверям\n\n"

        f"🔄 БЛОК 4: ВОЗВРАТЫ:\n"
        f"- {return_policy} Всегда ставь `[HUMAN_OVERRIDE]` при вопросах о возврате/замене.\n\n"

        f"🏪 БЛОК 5: АССОРТИМЕНТ И НАЛИЧИЕ ТОВАРОВ:\n"
        f"- Наш ассортимент: {specialization}."
    )
