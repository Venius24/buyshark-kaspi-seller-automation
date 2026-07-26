import logging
from backend.chatbot.config import load_chatbot_config

logger = logging.getLogger(__name__)

bot = None
dp = None
HAS_AIOGRAM = False

try:
    from aiogram import Bot, Dispatcher
    from aiogram.enums import ParseMode
    from aiogram.client.default import DefaultBotProperties
    from aiogram.client.session.aiohttp import AiohttpSession
    HAS_AIOGRAM = True
except ImportError:
    logger.warning("⚠️ Пакет 'aiogram' не установлен в окружении. Telegram-бот не будет активен.")

def init_bot():
    """
    Инициализирует экземпляр Telegram Bot и Dispatcher (Aiogram 3.x).
    Возвращает (bot, dp) или (None, None), если токен не задан или aiogram отсутствует.
    """
    global bot, dp
    if not HAS_AIOGRAM:
        return None, None

    cfg = load_chatbot_config()
    token = cfg.get("tg_bot_token", "").strip()

    if not token:
        logger.warning("⚠️ TG_BOT_TOKEN не задан. Telegram-уведомления для чат-бота выключены.")
        bot = None
        dp = None
        return None, None

    try:
        session = None
        proxy_url = cfg.get("tg_bot_proxy") or cfg.get("tg_proxy")
        if proxy_url:
            logger.info(f"🌐 Использование TG_BOT_PROXY для Telegram-бота: {proxy_url}")
            session = AiohttpSession(proxy=proxy_url)

        bot = Bot(
            token=token,
            session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
        )
        dp = Dispatcher()
        logger.info("🤖 Telegram Bot успешно инициализирован.")
        return bot, dp
    except Exception as e:
        logger.error(f"❌ Не удалось инициализировать Telegram Bot: {e}")
        bot = None
        dp = None
        return None, None
