import os
import base64
import logging
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

def _get_or_create_key() -> bytes:
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
