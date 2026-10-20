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

_FAILBACK_AFTER_SEC = 300


def parse_proxy_list(raw: str) -> list:
    """TG_BOT_PROXY: один URL или несколько через запятую (по порядку приоритета)."""
    return [p.strip() for p in (raw or "").split(",") if p.strip()]


if HAS_AIOGRAM:
    import asyncio
    import time

    import aiohttp
    from aiogram.exceptions import TelegramNetworkError

    _TRANSPORT_ERRORS = (TelegramNetworkError, aiohttp.ClientError, asyncio.TimeoutError, OSError)
    try:
        import aiohttp_socks
        _TRANSPORT_ERRORS += (
            aiohttp_socks.ProxyError,
            aiohttp_socks.ProxyConnectionError,
            aiohttp_socks.ProxyTimeoutError,
        )
    except (ImportError, AttributeError):
        pass

    class FallbackAiohttpSession(AiohttpSession):
        """Сессия с цепочкой прокси: при обрыве транспорта переходит к следующему.

        Через FAILBACK_AFTER_SEC после ухода с первого прокси пробует вернуться на него.
        """

        def __init__(self, proxies: list, **kwargs):
            super().__init__(proxy=proxies[0], **kwargs)
            self._proxies = proxies
            self._idx = 0
            self._switched_at = 0.0

        async def _switch_to(self, idx: int):
            self._idx = idx
            self._switched_at = time.monotonic() if idx else 0.0
            self.proxy = self._proxies[idx]  # сеттер пересоберёт коннектор
            await self.close()

        async def make_request(self, bot, method, timeout=None):
            if self._idx and time.monotonic() - self._switched_at > _FAILBACK_AFTER_SEC:
                await self._switch_to(0)
            last_exc = None
            for _ in range(len(self._proxies)):
                try:
                    return await super().make_request(bot, method, timeout)
                except _TRANSPORT_ERRORS as e:
                    last_exc = e
                    nxt = (self._idx + 1) % len(self._proxies)
                    logger.warning(
                        f"⚠️ TG-прокси #{self._idx} не отвечает ({type(e).__name__}), переключаюсь на #{nxt}"
                    )
                    await self._switch_to(nxt)
            raise last_exc


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
        proxies = parse_proxy_list(cfg.get("tg_bot_proxy") or cfg.get("tg_proxy"))
        if proxies:
            logger.info(f"🌐 TG-прокси для бота: {len(proxies)} шт. (с автопереключением)")
            session = FallbackAiohttpSession(proxies)

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
