import uuid
import secrets
import hashlib
import datetime
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks

from backend.models import ApiKeyCreateRequest, WebhookConfigRequest
from backend.state_helpers import (
    _list_api_keys,
    _create_api_key,
    _revoke_api_key,
    _load_webhook_config,
    _save_webhook_config
)
from backend.security.store_roles import store_section_guard
from backend.security.store_context import StoreContext, get_store_context
from backend.services.webhook_dispatcher import dispatch_webhook_event

router = APIRouter(prefix="/api/integrations", tags=["integrations"], dependencies=[Depends(store_section_guard("public-api"))])

@router.get("/keys")
def get_api_keys(ctx: StoreContext = Depends(get_store_context)):
    """[Dashboard UI] Список активных API-ключей этого магазина."""
    keys_dict = _list_api_keys(store_id=ctx.store_id)

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
def create_api_key(req: ApiKeyCreateRequest, ctx: StoreContext = Depends(get_store_context)):
    """[Dashboard UI] Сгенерировать новый API-ключ для 1С/ERP, привязанный к этому магазину (Секрет показывается один раз!)."""
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Укажите название ключа (например, Интеграция 1С:УНФ).")
    if not ctx.store_id:
        raise HTTPException(status_code=401, detail="Требуется вход в аккаунт с привязанным магазином.")

    key_id = f"key_{uuid.uuid4().hex[:8]}"
    raw_secret = f"bk_live_{secrets.token_hex(16)}"
    key_hash = hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    _create_api_key(
        store_id=ctx.store_id,
        key_id=key_id,
        name=req.name.strip(),
        key_hash=key_hash,
        prefix=f"{raw_secret[:11]}...",
        created_at=now_str,
    )

    return {
        "success": True,
        "message": "API-ключ успешно создан. Скопируйте секрет прямо сейчас (он больше не будет показан!).",
        "key_id": key_id,
        "api_secret": raw_secret,
        "name": req.name.strip()
    }

@router.delete("/keys/{key_id}")
def revoke_api_key(key_id: str, ctx: StoreContext = Depends(get_store_context)):
    """[Dashboard UI] Отозвать/удалить API-ключ — только если он принадлежит этому магазину."""
    if not ctx.store_id:
        raise HTTPException(status_code=401, detail="Требуется вход в аккаунт с привязанным магазином.")

    deleted_name = _revoke_api_key(store_id=ctx.store_id, key_id=key_id)
    if deleted_name is None:
        raise HTTPException(status_code=404, detail="API-ключ не найден.")
    return {"success": True, "message": f"API-ключ '{deleted_name}' успешно отозван."}

@router.get("/webhook")
def get_webhook_config(ctx: StoreContext = Depends(get_store_context)):
    """[Dashboard UI] Получить настройки Webhook этого магазина."""
    cfg = _load_webhook_config(store_id=ctx.store_id)
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
def save_webhook_config(req: WebhookConfigRequest, ctx: StoreContext = Depends(get_store_context)):
    """[Dashboard UI] Сохранить настройки Webhook этого магазина."""
    cfg = {
        "url": req.url.strip(),
        "secret": (req.secret or "").strip(),
        "enabled": req.enabled if req.enabled is not None else True,
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    _save_webhook_config(cfg, store_id=ctx.store_id)
    return {"success": True, "message": "Настройки Webhook успешно сохранены.", "config": cfg}

@router.post("/webhook/test")
async def test_webhook_dispatch(background_tasks: BackgroundTasks, ctx: StoreContext = Depends(get_store_context)):
    """[Dashboard UI] Отправить тестовое событие Webhook в 1С."""
    cfg = _load_webhook_config(store_id=ctx.store_id)
    if not cfg.get("url"):
        raise HTTPException(status_code=400, detail="Укажите и сохраните Webhook URL перед отправкой теста.")

    test_payload = {
        "ping": "test",
        "message": "Тестовое событие проверки связи с 1С из SaaS",
        "timestamp": datetime.datetime.now().isoformat()
    }
    background_tasks.add_task(dispatch_webhook_event, "test.ping", test_payload, ctx.store_id)

    return {
        "success": True,
        "message": f"Тестовый Webhook отправлен на {cfg.get('url')}. Проверьте логи 1С/ERP."
    }
