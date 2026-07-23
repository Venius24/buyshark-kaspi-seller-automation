import logging
from backend.chatbot import config, bot_instance

logger = logging.getLogger(__name__)

def register_handlers():
    """Регистрирует хэндлеры команд и инлайн-кнопок для Telegram бота."""
    dp = bot_instance.dp
    if not dp or not bot_instance.HAS_AIOGRAM:
        return

    from aiogram import types, F

    @dp.callback_query(F.data.startswith("unblock_"))
    async def process_unblock_callback(callback_query: types.CallbackQuery):
        group_id = callback_query.data.split("_")[1]
        
        if config.PROCESSED_CHATS.get(group_id) == "HUMAN_OVERRIDE":
            config.PROCESSED_CHATS.pop(group_id, None)
            
            original_text = callback_query.message.text or ""
            updated_text = original_text + "\n\n✅ **Чат разблокирован менеджером для ИИ.**"
            
            try:
                await callback_query.message.edit_text(
                    text=updated_text,
                    reply_markup=None
                )
            except Exception as e:
                logger.warning(f"⚠️ Не удалось обновить сообщение в Telegram: {e}")
                
            await callback_query.answer("Чат разблокирован для ИИ.")
        else:
            await callback_query.answer("Этот чат уже активен или не заблокирован.", show_alert=True)

    @dp.message(F.text == "/start")
    async def cmd_start(message: types.Message):
        admin_ids = config.load_admin_ids()
        if str(message.from_user.id) not in admin_ids:
            await message.reply("⛔ У вас нет доступа к управлению этим ботом.")
            return
        
        cfg = config.load_chatbot_config()
        await message.reply(
            f"👋 **Привет! Я ИИ-автоответчик Kaspi.**\n"
            f"🏪 **Merchant ID:** `{cfg.get('merchant_id')}`\n"
            f"Для проверки статуса используйте `/status`."
        )

    @dp.message(F.text == "/status")
    async def cmd_status(message: types.Message):
        admin_ids = config.load_admin_ids()
        if str(message.from_user.id) not in admin_ids:
            return
        
        cfg = config.load_chatbot_config()
        blocked_count = sum(1 for v in config.PROCESSED_CHATS.values() if v == "HUMAN_OVERRIDE")
        
        await message.reply(
            f"🤖 **Статус ИИ-автоответчика:** {'Включен ✅' if cfg.get('enabled') else 'Выключен ⏸️'}\n"
            f"🏪 **Merchant ID:** `{cfg.get('merchant_id')}`\n"
            f"🔒 **Заблокировано чатов (Human Override):** {blocked_count}\n"
            f"🔑 **Cookie статус:** {'🚨 Истекли' if config.COOKIE_ALERT_SENT else '✅ Валидны'}"
        )
