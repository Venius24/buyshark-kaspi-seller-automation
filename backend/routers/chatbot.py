import logging
import asyncio
import datetime as _dt
from typing import Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException

from backend.chatbot import config
from backend.chatbot import prompt as bot_prompt
from backend.chatbot import outbound as bot_outbound
from backend.chatbot.kaspi import load_chat_cookies
from backend.dumper.config import discover_merchant_id
from backend.security.store_context import StoreContext, get_store_context
from backend.security.store_roles import require_store_manager
from backend.state_helpers import _save_chat_cookies_for_store, _load_bot_usage_summary, _load_outbound_stats

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chatbot", tags=["chatbot"])

class ChatbotConfigRequest(BaseModel):
    enabled: Optional[bool] = True

class UnblockChatRequest(BaseModel):
    group_id: str

class MobileLoginStartRequest(BaseModel):
    phone: str

class MobileLoginSubmitRequest(BaseModel):
    code: str

class OutboundTestSendRequest(BaseModel):
    order_id: str
    message_text: str

class PickupPoint(BaseModel):
    name: str = ""
    address: str = ""
    map_url: str = ""
    hours: str = ""
    notes: str = ""

class BotProfileRequest(BaseModel):
    ai_name: Optional[str] = None
    store_display_name: Optional[str] = None
    specialization: Optional[str] = None
    return_policy: Optional[str] = None
    shipping_days: Optional[str] = None
    shipping_cutoff_time: Optional[str] = None
    pickup_points: Optional[List[PickupPoint]] = None
    human_triggers: Optional[List[str]] = None
    logistics_reply_ru: Optional[str] = None
    logistics_reply_kk: Optional[str] = None
    custom_instructions: Optional[str] = None
    custom_enabled: Optional[bool] = None
    review_delay_from_days: Optional[int] = None
    review_delay_to_days: Optional[int] = None
    review_send_overdue: Optional[bool] = None

@router.get("/config")
def get_chatbot_config(ctx: StoreContext = Depends(get_store_context)):
    """Получить конфигурацию ИИ-автоответчика этого магазина."""
    cfg = config.load_chatbot_config(ctx.store_id)

    return {
        "success": True,
        "config": {
            "enabled": cfg.get("enabled", True),
            "merchant_id": cfg.get("merchant_id", "")
        }
    }

@router.post("/config")
def save_chatbot_config(req: ChatbotConfigRequest, ctx: StoreContext = Depends(require_store_manager)):
    """Сохранить пользовательские настройки чат-бота (enabled) этого магазина."""
    update_data = {}
    if req.enabled is not None:
        update_data["enabled"] = req.enabled

    updated = config.save_chatbot_config(update_data, ctx.store_id)
    return {
        "success": True,
        "message": "Настройки уведомлений чат-бота сохранены.",
        "config": updated
    }

@router.get("/status")
def get_chatbot_status(ctx: StoreContext = Depends(get_store_context)):
    """Получить статус сессии, блокировки и активных диалогов чат-бота этого магазина."""
    cfg = config.load_chatbot_config(ctx.store_id)
    # Статус чата отражает отдельную SMS-сессию (encrypted_chat_cookies), а
    # не общие MC-куки email+пароль входа — чат-API Kaspi требует именно её.
    chat_cookies = load_chat_cookies(store_id=ctx.store_id)
    has_cookie_keys = bool("mc-session" in chat_cookies and "mc-sid" in chat_cookies)
    # Наличие ключей в куках ещё не значит, что сессия жива на стороне Kaspi —
    # это узнаётся только по факту реального запроса (chat_client/kaspi.py
    # шлёт notify_cookie_expired и взводит cookie_alert_sent при провале).
    # Раньше бейдж смотрел только на has_cookie_keys, поэтому "висел" зелёным
    # даже после того, как Telegram уже прислал алерт об истёкшей сессии.
    has_valid_cookies = has_cookie_keys and not config.is_cookie_alert_sent(ctx.store_id)

    blocked_chats = [
        {"group_id": gid, "reason": "HUMAN_OVERRIDE"}
        for gid in config.list_blocked_chat_ids(ctx.store_id)
    ]

    return {
        "success": True,
        "enabled": cfg.get("enabled", True),
        "has_session": has_valid_cookies,
        "cookie_alert_sent": config.is_cookie_alert_sent(ctx.store_id),
        "blocked_chats_count": len(blocked_chats),
        "blocked_chats": blocked_chats,
        "processed_chats_count": config.count_processed_chats(ctx.store_id)
    }

@router.post("/unblock")
def unblock_chat(req: UnblockChatRequest, ctx: StoreContext = Depends(require_store_manager)):
    """Разблокировать чат (HUMAN_OVERRIDE) этого магазина из дашборда UI."""
    gid = req.group_id.strip()
    if config.clear_chat_human_override(ctx.store_id, gid):
        return {"success": True, "message": f"Чат {gid} успешно разблокирован для ИИ."}

    return {"success": True, "message": f"Чат {gid} не был заблокирован."}

@router.post("/mobile-login/start")
def start_mobile_login(req: MobileLoginStartRequest, ctx: StoreContext = Depends(require_store_manager)):
    """
    Шаг 1 из 2 повторного входа в Kaspi-чат: отправляет телефон, Kaspi
    высылает SMS-код. Держит свой Chromium открытым до SUBMIT (де-
    глобализация Playwright — сессия SMS-входа привязана к store_id, не к
    общему на все магазины браузеру, см. playwright_worker.py).
    """
    from backend.services.playwright_worker import worker, Command
    success, msg, _cookies = worker.send_cmd(Command.START_SMS_LOGIN, req.phone, store_id=ctx.store_id)
    if success:
        return {"success": True, "message": "Код из SMS отправлен на указанный номер."}
    raise HTTPException(status_code=400, detail=msg)

@router.post("/mobile-login/submit-code")
def submit_mobile_login_code(req: MobileLoginSubmitRequest, ctx: StoreContext = Depends(require_store_manager)):
    """Шаг 2 из 2: код из SMS завершает вход, куки сохраняются в Store.encrypted_chat_cookies этого стора."""
    from backend.services.playwright_worker import worker, Command
    success, msg, fresh_cookies = worker.send_cmd(Command.SUBMIT_SMS_LOGIN, req.code, store_id=ctx.store_id)
    if not success:
        raise HTTPException(status_code=400, detail=msg)

    if not _save_chat_cookies_for_store(ctx.store_id, fresh_cookies):
        raise HTTPException(status_code=400, detail="Вход выполнен, но не удалось сохранить куки чата.")

    config.set_cookie_alert_sent(ctx.store_id, False)

    return {"success": True, "message": "Вход в чат Kaspi выполнен, сессия сохранена."}

@router.get("/profile")
def get_bot_profile(ctx: StoreContext = Depends(get_store_context)):
    """Профиль ИИ-менеджера этого магазина — поля универсального систем-промпта."""
    profile = bot_prompt.get_profile(ctx.store_id)
    return {"success": True, "profile": profile}

@router.post("/profile")
def save_bot_profile(req: BotProfileRequest, ctx: StoreContext = Depends(require_store_manager)):
    """Сохранить профиль ИИ-менеджера этого магазина. prompt_tier пересчитывается
    сервером от длины custom_instructions (символы) — тариф растёт с объёмом
    доп.инструкций владельца."""
    updates = {k: v for k, v in req.model_dump(exclude_unset=True).items() if v is not None}
    profile = bot_prompt.save_profile(ctx.store_id, updates)
    return {"success": True, "profile": profile}

@router.get("/usage")
def get_bot_usage(days: int = 30, ctx: StoreContext = Depends(get_store_context)):
    """Счётчик тарификации ИИ-бота этого магазина за период (Фаза 2). Пока
    просто цифра в кабинете — без реального инвойса/списания."""
    summary = _load_bot_usage_summary(ctx.store_id, days=days)
    return {"success": True, **summary}

@router.get("/outbound/queue")
def list_outbound_queue(limit: int = 20, ctx: StoreContext = Depends(get_store_context)):
    """Последние записи очереди рассылок этого магазина — для ручной проверки
    каркаса Фазы 3 из кабинета, до того как реальные кампании (Фазы 4-5)
    начнут ставить сообщения в очередь автоматически."""
    from backend.state_helpers import _get_sync_db_session
    from backend.db.models import OutboundQueueDB
    from sqlalchemy import select

    session = _get_sync_db_session()
    if not session:
        return {"success": True, "items": []}
    try:
        rows = session.execute(
            select(OutboundQueueDB)
            .where(OutboundQueueDB.store_id == ctx.store_id)
            .order_by(OutboundQueueDB.created_at.desc())
            .limit(limit)
        ).scalars().all()
        items = [{
            "id": r.id,
            "order_id": r.order_id,
            "campaign": r.campaign,
            "status": r.status,
            "group_id": r.group_id,
            "message_text": r.message_text,
            "scheduled_at": r.scheduled_at.isoformat() if r.scheduled_at else None,
            "sent_at": r.sent_at.isoformat() if r.sent_at else None,
            "skip_reason": r.skip_reason,
            "attempts": r.attempts,
        } for r in rows]
        return {"success": True, "items": items}
    finally:
        session.close()

@router.get("/outbound/stats")
def get_outbound_stats(days: int = 30, ctx: StoreContext = Depends(get_store_context)):
    """Статистика рассылок (Фаза 6) по кампаниям: sent/pending/needs_first_contact/
    skipped(+причины)/failed, и "доля ответивших" среди отправленных. Честная
    метка — НЕ "конверсия в отзывы": Kaspi не даёт связку отзыв↔заказ, дальше
    факта ответа клиента в чате идти нельзя."""
    stats = _load_outbound_stats(ctx.store_id, days=days)
    return {"success": True, **stats}

@router.post("/outbound/test-send")
async def test_send_outbound(req: OutboundTestSendRequest, ctx: StoreContext = Depends(require_store_manager)):
    """Ручной тест каркаса рассылок (Фаза 3): ставит сообщение в очередь с
    campaign='test' и сразу пытается его обработать (не ждёт тика фонового
    воркера outbound_loop, интервал 60с). Требует, чтобы по этому order_id
    УЖЕ была переписка — group_id резолвится HTTP-сканом getDiffGroups, без
    Playwright-первого-контакта (не реализован в Фазе 3, см.
    backend/chatbot/outbound.py)."""
    queued = bot_outbound.enqueue_outbound(
        store_id=ctx.store_id,
        order_id=req.order_id,
        campaign="test",
        message_text=req.message_text,
        scheduled_at=_dt.datetime.utcnow(),
    )
    if not queued:
        raise HTTPException(status_code=400, detail="Тестовое сообщение для этого order_id уже стоит/стояло в очереди.")

    cookies = load_chat_cookies(store_id=ctx.store_id)
    if not cookies:
        return {"success": True, "message": "Поставлено в очередь, но нет активной сессии чата (SMS) — отправится, когда сессия появится."}

    merchant_id = await asyncio.to_thread(discover_merchant_id, ctx.token)
    if not merchant_id:
        return {"success": True, "message": "Поставлено в очередь, но не удалось определить merchant_id — отправится на следующем тике."}

    await bot_outbound._process_store_queue(ctx.store_id, cookies, merchant_id)
    return {"success": True, "message": "Поставлено в очередь и обработано — проверьте статус через GET /api/chatbot/outbound/queue."}

@router.post("/outbound/first-contact-test")
async def test_first_contact(req: OutboundTestSendRequest, status: str = "PICKUP", ctx: StoreContext = Depends(require_store_manager)):
    """РУЧНОЙ тест Playwright-первого-контакта (Фаза 4) — открывает браузер,
    находит заказ в кабинете, кликает «Сообщения по заказу» и пытается
    отправить сообщение через GUI-виджет. Требует активной SMS-чат-сессии
    магазина (телефон+SMS вход, ~5 часов) — обычный email+пароль вход для
    этого виджета не подходит. НЕ подключено к автоворкеру рассылок — сам
    скрипт (в отличие от селекторов/логики, которые уже проверены живьём
    вручную) ещё не прогонялся end-to-end через реальную SMS-сессию, поэтому
    вызывается только вручную, с осознанным решением владельца магазина
    отправить именно это сообщение по именно этому заказу."""
    from backend.services.playwright_first_contact import send_first_contact

    # Чаты Kaspi доступны ИСКЛЮЧИТЕЛЬНО через SMS-сессию (~5 часов) — обычный
    # email+пароль вход в кабинет (_load_mc_cookies) для виджета не подходит,
    # даже когда виджет открыт со страницы карточки заказа в kaspi.kz/mc.
    cookies = load_chat_cookies(store_id=ctx.store_id)
    if not cookies:
        raise HTTPException(status_code=400, detail="Нет активной SMS-чат-сессии магазина (телефон+SMS вход, сессия живёт ~5 часов).")

    success, message = await asyncio.to_thread(send_first_contact, cookies, req.order_id, req.message_text, status)
    if not success:
        raise HTTPException(status_code=400, detail=message)
    return {"success": True, "message": message}
