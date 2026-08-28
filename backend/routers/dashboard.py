import json
import logging
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.db.database import get_db
from backend.db.models import User, DashboardLayoutDB, DashboardRoleDefaultsDB
from backend.routers.auth_v1 import get_current_user
from backend.security.store_context import StoreContext, get_store_context
from backend.security.store_roles import require_store_owner

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Держать синхронно с id виджетов во frontend/src/dashboard/widgetRegistry.ts —
# это whitelist, защищающий от мусора/опечаток в сохраняемом layout_json.
KNOWN_WIDGET_IDS = {
    "score_ring",
    "saved_margin",
    "price_lead_gauge",
    "revenue_heatmap",
    "sales_stats",
    "price_radar",
    "abc_xyz",
    "competitor_leaderboard",
    "catalog_coverage",
    "hourly_heatmap",
    "chat_activity",
    "system_status",
    "quick_actions",
}


class WidgetLayoutItem(BaseModel):
    id: str
    visible: bool = True
    order: int = 0
    chartType: Optional[str] = None
    metrics: Optional[List[str]] = None
    period: Optional[int] = None


class DashboardLayoutRequest(BaseModel):
    widgets: List[WidgetLayoutItem]


class RoleDefaultWidgetItem(BaseModel):
    id: str
    visible: bool = True


class DashboardRoleDefaultsRequest(BaseModel):
    role: str
    widgets: List[RoleDefaultWidgetItem]


@router.get("/layout")
async def get_dashboard_layout(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Персональная раскладка дашборда. Пустой список widgets означает, что
    пользователь ещё не менял раскладку — фронт мёржит с дефолтом из своего
    реестра виджетов."""
    result = await db.execute(select(DashboardLayoutDB).where(DashboardLayoutDB.user_id == user.id))
    row = result.scalar_one_or_none()
    if not row:
        return {"success": True, "widgets": []}
    try:
        parsed = json.loads(row.layout_json or "{}")
    except json.JSONDecodeError:
        parsed = {}
    return {"success": True, "widgets": parsed.get("widgets", [])}


@router.put("/layout")
async def save_dashboard_layout(
    req: DashboardLayoutRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    unknown = {w.id for w in req.widgets} - KNOWN_WIDGET_IDS
    if unknown:
        raise HTTPException(status_code=400, detail=f"Неизвестные id виджетов: {sorted(unknown)}")

    layout_json = json.dumps({"widgets": [w.model_dump(exclude_none=True) for w in req.widgets]}, ensure_ascii=False)

    result = await db.execute(select(DashboardLayoutDB).where(DashboardLayoutDB.user_id == user.id))
    row = result.scalar_one_or_none()
    if row:
        row.layout_json = layout_json
        row.updated_at = datetime.utcnow()
    else:
        row = DashboardLayoutDB(user_id=user.id, layout_json=layout_json, updated_at=datetime.utcnow())
        db.add(row)
    await db.commit()

    return {"success": True, "widgets": [w.model_dump(exclude_none=True) for w in req.widgets]}


@router.get("/role-defaults")
async def get_dashboard_role_defaults(
    role: str,
    ctx: StoreContext = Depends(get_store_context),
    db: AsyncSession = Depends(get_db),
):
    """Ролевые дефолты видимости, настроенные OWNER'ом этого магазина. Читать
    может любой член магазина — самому MANAGER/VIEWER это нужно для мержа
    собственного дашборда."""
    result = await db.execute(
        select(DashboardRoleDefaultsDB).where(
            DashboardRoleDefaultsDB.store_id == ctx.store_id,
            DashboardRoleDefaultsDB.role == role,
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        return {"success": True, "widgets": []}
    try:
        parsed = json.loads(row.layout_json or "{}")
    except json.JSONDecodeError:
        parsed = {}
    return {"success": True, "widgets": parsed.get("widgets", [])}


@router.put("/role-defaults")
async def save_dashboard_role_defaults(
    req: DashboardRoleDefaultsRequest,
    ctx: StoreContext = Depends(require_store_owner),
    db: AsyncSession = Depends(get_db),
):
    if req.role not in ("MANAGER", "VIEWER"):
        raise HTTPException(status_code=400, detail="role должен быть MANAGER или VIEWER")
    unknown = {w.id for w in req.widgets} - KNOWN_WIDGET_IDS
    if unknown:
        raise HTTPException(status_code=400, detail=f"Неизвестные id виджетов: {sorted(unknown)}")

    layout_json = json.dumps({"widgets": [w.model_dump() for w in req.widgets]}, ensure_ascii=False)

    result = await db.execute(
        select(DashboardRoleDefaultsDB).where(
            DashboardRoleDefaultsDB.store_id == ctx.store_id,
            DashboardRoleDefaultsDB.role == req.role,
        )
    )
    row = result.scalar_one_or_none()
    if row:
        row.layout_json = layout_json
        row.updated_at = datetime.utcnow()
    else:
        row = DashboardRoleDefaultsDB(store_id=ctx.store_id, role=req.role, layout_json=layout_json, updated_at=datetime.utcnow())
        db.add(row)
    await db.commit()

    return {"success": True, "role": req.role, "widgets": [w.model_dump() for w in req.widgets]}
