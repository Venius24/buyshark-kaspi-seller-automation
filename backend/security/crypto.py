import os
import base64
import logging
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

def _load_env_if_needed():
    """Подстраховка от одного и того же класса бага: этот модуль читает
    ENCRYPTION_KEY один раз при импорте (см. _get_or_create_key ниже), а
    main.py/chatbot/config.py грузят .env ТОЛЬКО в своих модулях. Любой
    одноразовый скрипт/shell-вызов, который импортирует backend.security.crypto
    раньше, чем .env оказался в os.environ, тихо ловил случайный ключ на
    процесс — всё, что он шифровал, становилось нечитаемым для всех остальных
    процессов навсегда (так испортились токены в локальной dev БД, см.
    HANDOFF.md). Грузим .env здесь же, чтобы источник не зависел от того,
    что вызывающий код это уже сделал."""
    if os.environ.get("ENCRYPTION_KEY"):
        return
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env_path = os.path.join(base_dir, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _get_or_create_key() -> bytes:
    _load_env_if_needed()
    key_str = os.getenv("ENCRYPTION_KEY")
    if key_str:
        try:
            # Ensure key is valid Fernet key
            key_bytes = key_str.encode("utf-8")
            Fernet(key_bytes)
            return key_bytes
        except Exception:
            pass

    # Generate new Fernet key if missing or invalid
    generated_key = Fernet.generate_key()
    os.environ["ENCRYPTION_KEY"] = generated_key.decode("utf-8")
    return generated_key

_FERNET_KEY = _get_or_create_key()
_cipher_suite = Fernet(_FERNET_KEY)

def encrypt_data(plain_text: str) -> str:
    """Шифрует чувствительные данные (куки, API ключи) методом AES-256 Fernet"""
    if not plain_text:
        return ""
    encrypted_bytes = _cipher_suite.encrypt(plain_text.encode("utf-8"))
    return encrypted_bytes.decode("utf-8")

def decrypt_data(cipher_text: str) -> str:
    """Расшифровывает зашифрованные данные на лету"""
    if not cipher_text:
        return ""
    try:
        decrypted_bytes = _cipher_suite.decrypt(cipher_text.encode("utf-8"))
        return decrypted_bytes.decode("utf-8")
    except Exception as e:
        logger.error(f" Ошибка расшифровки секретов: {e}")
        return ""
