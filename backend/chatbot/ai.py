import logging
import httpx
from backend.chatbot.config import load_chatbot_config
from backend.chatbot.prompt import get_profile, render_system_prompt

logger = logging.getLogger(__name__)

async def generate_ai_reply(client_name: str, item_title: str, history: list, store_id: str | None = None, awaiting_review_reply: bool = False) -> tuple[str, bool, dict]:
    """
    Асинхронно генерирует ответ клиенту через DeepSeek API с помощью httpx.AsyncClient.
    Систем-промпт собирается из профиля этого магазина (backend/chatbot/prompt.py),
    а не хардкодится под конкретный магазин.
    awaiting_review_reply (Фаза 5) — прокидывается в систем-промпт, чтобы модель
    распознала негативный ответ на кампанию «Отзыв» и поставила [HUMAN_OVERRIDE].
    Возвращает (reply_text, is_human_override, usage) — usage = {"model", "prompt_tokens",
    "completion_tokens"} для тарификации (Фаза 2), пустой dict если вызов до DeepSeek не дошёл.
    """
    cfg = load_chatbot_config()
    api_key = (cfg.get("deepseek_api") or cfg.get("deepseek_api_key") or "").strip()
    model = "deepseek-chat"

    if not api_key:
        logger.warning("⚠️ DEEPSEEK_API не установлен в .env. Автоматический вызов менеджера.")
        return "Здравствуйте! Приняли ваше обращение. Сейчас менеджер проверит детали заказа и ответит вам.", True, {}

    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    history_str = "\n".join(history)

    profile = get_profile(store_id)
    system_prompt = render_system_prompt(profile, client_name, item_title, awaiting_review_reply=awaiting_review_reply)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Вся история диалога (последнее сообщение в самом низу):\n{history_str}\n\nНапиши точный ответ Магазина на последнее сообщение:"}
        ],
        "temperature": 0.2
    }

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                body = resp.json()
                content = body["choices"][0]["message"]["content"].strip()
                usage_raw = body.get("usage") or {}
                usage = {
                    "model": model,
                    "prompt_tokens": usage_raw.get("prompt_tokens"),
                    "completion_tokens": usage_raw.get("completion_tokens"),
                }
                if "[HUMAN_OVERRIDE]" in content:
                    content = content.replace("[HUMAN_OVERRIDE]", "").strip()
                    return content, True, usage
                return content, False, usage
            else:
                logger.error(f"💥 Ошибка DeepSeek API: HTTP {resp.status_code} - {resp.text}")
    except Exception as e:
        logger.error(f"💥 Сбой при асинхронном вызове DeepSeek API: {e}")

    return "Здравствуйте! Приняли ваше обращение. Сейчас менеджер проверит детали заказа и ответит вам.", True, {}
