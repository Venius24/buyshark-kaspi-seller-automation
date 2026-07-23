import os
import json
import logging

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATES_DIR = os.path.join(BASE_DIR, "states")
CONFIG_FILE = os.path.join(STATES_DIR, "chatbot_config.json")
SESSION_FILE = os.path.join(STATES_DIR, "session.json")
ADMIN_IDS_FILE = os.path.join(STATES_DIR, "admin_ids.txt")

# ── Загрузка .env ──
def load_env():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")
        except Exception as e:
            logger.warning(f"Ошибка чтения .env: {e}")

load_env()

# Глобальные флаги состояния в памяти
COOKIE_ALERT_SENT = False
PROCESSED_CHATS = {}  # group_id -> msg_id или "HUMAN_OVERRIDE"

LOGISTICS_TRIGGERS = {
    "ru": {
        "keywords": ["доставка", "когда приедет", "где заказ", "задерживается", "доставили", "курьер", "почему долго"],
        "reply": "Мы бы с радостью подсказали, но всю логистику и доставку полностью организует сам Kaspi. Мы собираем и передаем заказы вовремя, а дальше за движение посылки отвечает курьерская служба платформы. Пожалуйста, напишите в службу поддержки 'Kaspi Гид' прямо в приложении — они видят точное местоположение курьера и быстро помогут!"
    },
    "kk": {
        "keywords": ["жеткізу", "қашан келеді", "тапсырыс қайда", "кешігіп", "курьер", "неге ұзақ"],
        "reply": "Біз тапсырысты уақытында жинап, курьерге өткізіп жібердік. Алайда, жеткізу жұмыстарымен толықтай Kaspi-дің өзі айналысады. Тапсырыстың қазір қай жерде екенін білу үшін қосымшадағы 'Kaspi Гид' қолдау қызметіне жазсаңыз, олар курьермен байланысып, сізге тез арада көмектеседі!"
    }
}

def load_chatbot_config() -> dict:
    """Загружает динамическую конфигурацию чат-бота из states/chatbot_config.json или .env."""
    default_config = {
        "enabled": True,
        "deepseek_api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        "tg_bot_token": os.environ.get("TG_BOT_TOKEN", ""),
        "merchant_id": os.environ.get("MERCHANT_ID", "20186986"),
        "admin_ids": ["7119137844", "8661441552"],
        "use_tg_proxy": os.environ.get("USE_TG_PROXY", "false").lower() in ("true", "1", "yes", "on"),
        "tg_proxy": os.environ.get("TG_PROXY", "")
    }

    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_config.update({k: v for k, v in data.items() if v is not None})
        except Exception as e:
            logger.error(f"Ошибка чтения {CONFIG_FILE}: {e}")

    # Перепроверяем fallback admin_ids если пустые
    if not default_config.get("admin_ids"):
        default_config["admin_ids"] = load_admin_ids()

    return default_config

def save_chatbot_config(new_config: dict) -> dict:
    """Сохраняет конфигурацию чат-бота в states/chatbot_config.json."""
    os.makedirs(STATES_DIR, exist_ok=True)
    current = load_chatbot_config()
    current.update(new_config)
    
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
        
    logger.info("⚙️ Конфигурация чат-бота успешно обновлена.")
    return current

def load_admin_ids() -> list:
    """Загружает ID получателей уведомлений Telegram из файла или конфига."""
    cfg = load_chatbot_config()
    if cfg.get("admin_ids"):
        return cfg["admin_ids"]

    if os.path.exists(ADMIN_IDS_FILE):
        try:
            with open(ADMIN_IDS_FILE, "r", encoding="utf-8") as f:
                ids = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
                if ids:
                    return ids
        except Exception as e:
            logger.error(f"Ошибка чтения {ADMIN_IDS_FILE}: {e}")
            
    return ["7119137844", "8661441552"]
