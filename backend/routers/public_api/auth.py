import hashlib
import datetime
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

from backend.state_helpers import _find_api_key_by_hash, _touch_api_key_last_used

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def get_api_key_user(x_api_key: str = Security(api_key_header)):
    """
    Зависимость проверки публичного API-ключа (X-API-Key).
    Вычисляет SHA-256 хеш ключа и ищет владельца в ApiKeyDB (Слайс 3) — до
    этого ключи вообще не были привязаны к магазину, любой валидный ключ
    открывал доступ к данным ЛЮБОГО стора. Теперь возвращает store_id
    владельца — вызывающий код обязан скоупить все операции им.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Отсутствует заголовок X-API-Key."
        )

    key_clean = x_api_key.strip()
    key_hash = hashlib.sha256(key_clean.encode("utf-8")).hexdigest()

    matched = _find_api_key_by_hash(key_hash)
    if not matched:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный или отозванный API-ключ."
        )

    _touch_api_key_last_used(matched["key_id"], datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    return {
        "key_id": matched["key_id"],
        "name": matched["name"],
        "store_id": matched["store_id"],
    }
