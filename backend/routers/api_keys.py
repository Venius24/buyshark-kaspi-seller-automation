import uuid
import secrets
import hashlib
import datetime
from fastapi import APIRouter, HTTPException, BackgroundTasks

from backend.models import ApiKeyCreateRequest, WebhookConfigRequest
from backend.state_helpers import (
    _require_token,
    _load_api_keys,
    _save_api_keys,
    _load_webhook_config,
    _save_webhook_config
)
from backend.services.webhook_dispatcher import dispatch_webhook_event

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

@router.get("/keys")
def get_api_keys():
    """[Dashboard UI] Список активных API-ключей."""
    _require_token()
    keys_dict = _load_api_keys()
    
    result = []
    for kid, info in keys_dict.items():
        result.append({
            "key_id": kid,
            "name": info.get("name"),
            "prefix": info.get("prefix", "bk_live_..."),
            "created_at": info.get("created_at"),
            "last_used": info.get("last_used"),
            "active": info.get("active", True)
        })

    result.sort(key=lambda k: k.get("created_at", ""), reverse=True)
    return {"success": True, "keys": result, "count": len(result)}

@router.post("/keys")
def create_api_key(req: ApiKeyCreateRequest):
    """[Dashboard UI] Сгенерировать новый API-ключ для 1С/ERP (Секрет показывается один раз!)."""
    _require_token()
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Укажите название ключа (например, Интеграция 1С:УНФ).")

    key_id = f"key_{uuid.uuid4().hex[:8]}"
    raw_secret = f"bk_live_{secrets.token_hex(16)}"
    key_hash = hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    keys = _load_api_keys()
    keys[key_id] = {
        "key_id": key_id,
        "name": req.name.strip(),
        "hash": key_hash,
        "prefix": f"{raw_secret[:11]}...",
        "created_at": now_str,
        "last_used": None,
        "active": True
    }
    _save_api_keys(keys)

    return {
        "success": True,
        "message": "API-ключ успешно создан. Скопируйте секрет прямо сейчас (он больше не будет показан!).",
        "key_id": key_id,
        "api_secret": raw_secret,
        "name": req.name.strip()
    }

@router.delete("/keys/{key_id}")
def revoke_api_key(key_id: str):
    """[Dashboard UI] Отозвать/удалить API-ключ."""
    _require_token()
    keys = _load_api_keys()
    if key_id not in keys:
        raise HTTPException(status_code=404, detail="API-ключ не найден.")

    deleted = keys.pop(key_id)
    _save_api_keys(keys)
    return {"success": True, "message": f"API-ключ '{deleted.get('name')}' успешно отозван."}

@router.get("/webhook")
def get_webhook_config():
    """[Dashboard UI] Получить настройки Webhook."""
    _require_token()
    cfg = _load_webhook_config()
    secret = cfg.get("secret", "")
    masked_secret = (secret[:4] + "..." + secret[-4:]) if len(secret) > 8 else secret

    return {
        "success": True,
        "config": {
            "url": cfg.get("url", ""),
            "secret": secret,
            "secret_masked": masked_secret,
            "enabled": cfg.get("enabled", True)
        }
    }

@router.post("/webhook")
def save_webhook_config(req: WebhookConfigRequest):
    """[Dashboard UI] Сохранить настройки Webhook."""
    _require_token()
    cfg = {
        "url": req.url.strip(),
        "secret": (req.secret or "").strip(),
        "enabled": req.enabled if req.enabled is not None else True,
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    _save_webhook_config(cfg)
    return {"success": True, "message": "Настройки Webhook успешно сохранены.", "config": cfg}

@router.post("/webhook/test")
async def test_webhook_dispatch(background_tasks: BackgroundTasks):
    """[Dashboard UI] Отправить тестовое событие Webhook в 1С."""
    _require_token()
    cfg = _load_webhook_config()
    if not cfg.get("url"):
        raise HTTPException(status_code=400, detail="Укажите и сохраните Webhook URL перед отправкой теста.")

    test_payload = {
        "ping": "test",
        "message": "Тестовое событие проверки связи с 1С из SaaS",
        "timestamp": datetime.datetime.now().isoformat()
    }
    background_tasks.add_task(dispatch_webhook_event, "test.ping", test_payload)

    return {
        "success": True,
        "message": f"Тестовый Webhook отправлен на {cfg.get('url')}. Проверьте логи 1С/ERP."
    }
