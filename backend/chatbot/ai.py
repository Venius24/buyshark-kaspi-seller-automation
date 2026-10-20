import logging
import datetime as _dt
from backend.chatbot.config import load_chatbot_config
from backend.chatbot.llm import STRUCTURED_INSTRUCTIONS, complete, load_llm_config
from backend.chatbot.prompt import get_profile, prompt_fingerprint, render_system_prompt

logger = logging.getLogger(__name__)

HOLD_FALLBACK_REPLY = "Здравствуйте! Приняли ваше обращение. Сейчас менеджер проверит детали заказа и ответит вам."

# Сообщения без текста. Раньше они уходили в модель пустой строкой «Клиент: »,
# и модель не знала, что клиент вообще что-то прислал.
_MEDIA_RU = {"IMAGE": "фото", "VIDEO": "видео", "AUDIO": "голосовое сообщение"}


def _message_text(m: dict) -> str:
    return (m.get("text") or (m.get("ext") or {}).get("text") or "").strip()


def build_dialog(raw_messages: list, bot_texts: set | None = None, tracking_since: _dt.datetime | None = None) -> list:
    """Сырая история Kaspi -> сообщения chat/completions.

    sender="messenger" — покупатель (user), sender="client" — магазин
    (assistant); да, наоборот, см. KASPI_CHAT_API.md. Сообщение магазина,
    которое бот не отправлял (нет среди bot_texts), помечается как ответ
    менеджера — но только после tracking_since, с которого ответы бота
    журналируются с текстом; до этого момента отличить их нельзя.
    Подряд идущие реплики одной стороны склеиваются."""
    bot_texts = bot_texts or set()
    since_ms = int(tracking_since.replace(tzinfo=_dt.timezone.utc).timestamp() * 1000) if tracking_since else None
    dialog: list = []
    for m in raw_messages:
        from_client = m.get("sender") == "messenger"
        text = _message_text(m)
        if not text:
            media = _MEDIA_RU.get(str(m.get("messageType") or "").upper(), "вложение")
            text = f"[клиент прислал {media}]" if from_client else f"[магазин отправил {media}]"
        role = "user" if from_client else "assistant"
        if (
            not from_client
            and since_ms is not None
            and (m.get("created") or 0) >= since_ms
            and text not in bot_texts
        ):
            text = f"[ответил менеджер магазина] {text}"
        if dialog and dialog[-1]["role"] == role:
            dialog[-1]["content"] += "\n" + text
        else:
            dialog.append({"role": role, "content": text})
    return dialog


async def generate_ai_reply(
    client_name: str,
    item_title: str,
    history: list,
    store_id: str | None = None,
    awaiting_review_reply: bool = False,
    facts_block: str = "",
    client_text: str = "",
    profile: dict | None = None,
    now: _dt.datetime | None = None,
) -> tuple[str, bool, dict]:
    """
    Генерирует ответ клиенту через LLM (backend/chatbot/llm.py: конфиг, ретраи,
    structured output, трасса).
    Систем-промпт собирается из профиля этого магазина (backend/chatbot/prompt.py),
    а не хардкодится под конкретный магазин.

    history — диалог из build_dialog() (список {"role", "content"}). Если он
    пуст (история не загрузилась), в модель уходит одно последнее сообщение
    клиента client_text.
    facts_block — данные заказа и наличия (backend/chatbot/order_context.py).
    awaiting_review_reply (Фаза 5) — прокидывается в систем-промпт, чтобы модель
    распознала негативный ответ на кампанию «Отзыв» и поставила [HUMAN_OVERRIDE].
    Возвращает (reply_text, is_human_override, usage). usage — трасса вызова:
    model, prompt_tokens, completion_tokens (тарификация, Фаза 2), latency_ms,
    attempts, cost_usd, structured, prompt_version, handoff_reason. При сбое —
    HOLD-ответ, вызов менеджера и usage["error"] (токенов в нём нет).
    profile и now передают evals (evals/autoresponder): профиль тестового
    магазина вместо чтения из БД и время, на которое собирается промпт.
    """
    cfg = load_chatbot_config()
    api_key = (cfg.get("deepseek_api") or cfg.get("deepseek_api_key") or "").strip()

    if not api_key:
        logger.warning("⚠️ DEEPSEEK_API не установлен в .env. Автоматический вызов менеджера.")
        return HOLD_FALLBACK_REPLY, True, {"error": "нет ключа DEEPSEEK_API"}

    llm_cfg = load_llm_config()
    if profile is None:
        profile = get_profile(store_id)
    system_prompt = render_system_prompt(
        profile, client_name, item_title,
        awaiting_review_reply=awaiting_review_reply, facts_block=facts_block, now=now,
    )
    if llm_cfg.structured:
        system_prompt += STRUCTURED_INSTRUCTIONS

    dialog = list(history or [])
    if not dialog or dialog[-1]["role"] != "user":
        # Последней репликой обязан быть клиент — иначе отвечать не на что.
        dialog.append({"role": "user", "content": client_text or "[клиент написал сообщение]"})

    result = await complete([{"role": "system", "content": system_prompt}, *dialog], api_key, llm_cfg)
    usage = {**result.trace, "prompt_version": prompt_fingerprint()}
    if not result.ok:
        return HOLD_FALLBACK_REPLY, True, usage
    if result.handoff_reason:
        usage["handoff_reason"] = result.handoff_reason
    return result.reply, result.handoff, usage
