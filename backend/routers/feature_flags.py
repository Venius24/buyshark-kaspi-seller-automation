import logging
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends

from backend import feature_flags
from backend.chatbot import config as chatbot_config
from backend.security.store_context import StoreContext, get_store_context
from backend.security.store_roles import require_store_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/feature-flags", tags=["feature-flags"])


class FeatureFlagsRequest(BaseModel):
    dumping_enabled: Optional[bool] = None
    stock_groups_enabled: Optional[bool] = None
    autoassembly_enabled: Optional[bool] = None
    chatbot_enabled: Optional[bool] = None


def _current_flags(store_id: str) -> dict:
    flags = feature_flags.get_flags(store_id)
    flags["chatbot_enabled"] = chatbot_config.load_chatbot_config(store_id).get("enabled", True)
    return flags


@router.get("")
def get_feature_flags(ctx: StoreContext = Depends(get_store_context)):
    """Состояние всех общемагазинных выключателей — вкладка "Глобальное управление"."""
    return {"success": True, "flags": _current_flags(ctx.store_id)}


@router.post("")
def save_feature_flags(req: FeatureFlagsRequest, ctx: StoreContext = Depends(require_store_manager)):
    """Частичное обновление выключателей. Не требует активного тарифа — выключить
    автоматику можно всегда, воркеры и так перепроверяют billing-гейт отдельно."""
    feature_flags.set_flags(
        ctx.store_id,
        dumping_enabled=req.dumping_enabled,
        stock_groups_enabled=req.stock_groups_enabled,
        autoassembly_enabled=req.autoassembly_enabled,
    )
    if req.chatbot_enabled is not None:
        chatbot_config.save_chatbot_config({"enabled": req.chatbot_enabled}, ctx.store_id)

    return {"success": True, "flags": _current_flags(ctx.store_id)}
