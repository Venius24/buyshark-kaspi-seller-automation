import hashlib
import datetime
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader

from backend.state_helpers import _load_api_keys, _save_api_keys

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def get_api_key_user(x_api_key: str = Security(api_key_header)):
    """
    Зависимость проверки публичного API-ключа (X-API-Key).
    Вычисляет SHA-256 хеш ключа и сверяет с сохраненными в states/api_keys.json.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Отсутствует заголовок X-API-Key."
        )

    key_clean = x_api_key.strip()
    key_hash = hashlib.sha256(key_clean.encode("utf-8")).hexdigest()

    keys = _load_api_keys()
    
    matched_key_id = None
    matched_info = None

    for kid, info in keys.items():
        if info.get("hash") == key_hash and info.get("active", True):
            matched_key_id = kid
            matched_info = info
            break

    if not matched_info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Недействительный или отозванный API-ключ."
        )

    # Обновляем timestamp последнего использования
    matched_info["last_used"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    keys[matched_key_id] = matched_info
    _save_api_keys(keys)

    return {
        "key_id": matched_key_id,
        "name": matched_info.get("name"),
        "account_id": matched_info.get("account_id", "default")
    }
