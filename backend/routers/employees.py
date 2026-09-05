"""Сотрудники магазина: кто ещё имеет доступ в кабинет и к чему именно.

До этого магазин = один аккаунт: владелец либо отдавал свой логин сборщику
целиком (вместе с ценами, демпингом и балансом), либо заводил сотрудника
через Telegram-бота (`chatbot/telegram_identity.py`) — но такой аккаунт
намеренно не имеет входа в веб-кабинет. Здесь появляется веб-путь.

Права сотрудника — две оси (обоснование в backend/security/sections.py):
роль (глубина: VIEWER/MANAGER) и разделы (ширина). Владелец выбирает
должностной пресет («Сборщик», «Менеджер чата»), при желании правит галочки
разделов вручную. Хранится и то, и другое: пресет — как метка для UI, права
— как факт, чтобы правка пресета в коде не меняла права уже заведённых людей.

Пароль сотрудника генерируется здесь и показывается владельцу ОДИН раз:
внешней почты у платформы нет, слать приглашение некуда. Владелец видел
пароль, поэтому у аккаунта поднят `must_change_password` — до смены пароля
кабинет не пускает дальше экрана смены (см. routers/auth_v1.py).
"""
import asyncio
import json
import logging
import secrets
import string
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.database import get_db
from backend.db.models import User, UserStore
from backend.security.auth import hash_password
from backend.security.sections import (
    JOB_PRESETS,
    PRESETS_BY_CODE,
    SECTIONS,
    effective_sections,
    sanitize_sections,
)
from backend.security.store_context import StoreContext
from backend.security.store_roles import require_store_owner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/employees", tags=["employees"])

EMPLOYEE_ROLES = ("MANAGER", "VIEWER")

# Символы пароля без 0/O/1/l/I — временный пароль владелец диктует сотруднику
# голосом или переписывает от руки, и «ноль или буква О» тут стоит дороже,
# чем два бита энтропии.
_PASSWORD_ALPHABET = "".join(
    c for c in (string.ascii_letters + string.digits) if c not in "0O1lI"
)


def _generate_password(length: int = 12) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


class EmployeeOut(BaseModel):
    user_id: str
    email: str
    full_name: Optional[str] = None
    role: str
    job_preset: Optional[str] = None
    job_title: Optional[str] = None
    sections: List[str]
    is_active: bool
    must_change_password: bool
    is_telegram_only: bool
    created_at: Optional[str] = None


class EmployeeCreateRequest(BaseModel):
    email: EmailStr
    full_name: Optional[str] = Field(default=None, max_length=255)
    preset: str = Field(default="PACKER")
    # Ручная правка набора разделов поверх пресета. None = взять из пресета.
    sections: Optional[List[str]] = None
    job_title: Optional[str] = Field(default=None, max_length=100)


class EmployeeUpdateRequest(BaseModel):
    preset: Optional[str] = None
    role: Optional[str] = None
    sections: Optional[List[str]] = None
    job_title: Optional[str] = Field(default=None, max_length=100)
    is_active: Optional[bool] = None


def _row_to_out(user: User, us: UserStore) -> EmployeeOut:
    raw = us.allowed_sections
    parsed: Optional[List[str]] = None
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                parsed = [str(x) for x in loaded]
        except (ValueError, TypeError):
            logger.warning("allowed_sections не разобрался (user=%s store=%s)", us.user_id, us.store_id)
    return EmployeeOut(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=us.role,
        job_preset=us.job_preset,
        job_title=us.job_title,
        sections=sorted(effective_sections(us.role, parsed)),
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        is_telegram_only=user.is_telegram_only,
        created_at=us.created_at.isoformat() if us.created_at else None,
    )


async def _count_employees(db: AsyncSession, store_id: str) -> int:
    """Сколько мест занято. Считаются все не-владельцы, включая заведённых
    через Telegram и временно деактивированных: место в тарифе занимает сам
    факт существования доступа, а не то, зашёл ли человек сегодня."""
    res = await db.execute(
        select(func.count(UserStore.user_id)).where(
            UserStore.store_id == store_id, UserStore.role != "OWNER"
        )
    )
    return res.scalar() or 0


async def _load_target(db: AsyncSession, store_id: str, user_id: str) -> tuple[User, UserStore]:
    res = await db.execute(
        select(User, UserStore)
        .join(UserStore, User.id == UserStore.user_id)
        .where(UserStore.store_id == store_id, UserStore.user_id == user_id)
    )
    row = res.first()
    if not row:
        raise HTTPException(status_code=404, detail="Сотрудник не найден в этом магазине")
    user, us = row
    if us.role == "OWNER":
        # Владельцев через этот роутер не трогаем вообще: передача магазина
        # другому владельцу — отдельная операция со своими последствиями
        # (деньги, креды, тариф), а не частный случай правки прав сотрудника.
        raise HTTPException(status_code=403, detail="Права владельца магазина через раздел «Сотрудники» не меняются")
    return user, us


@router.get("/catalog")
async def get_catalog(ctx: StoreContext = Depends(require_store_owner)):
    """Справочник разделов и должностных пресетов для UI — чтобы фронтенд не
    держал вторую копию списка, расходящуюся с бэкендом при каждой правке."""
    return {
        "sections": [
            {"key": s.key, "title": s.title, "owner_only": s.owner_only} for s in SECTIONS
        ],
        "presets": [
            {
                "code": p.code,
                "title": p.title,
                "role": p.role,
                "sections": sanitize_sections(p.sections),
                "hint": p.hint,
            }
            for p in JOB_PRESETS
        ],
        "roles": [
            {"code": "MANAGER", "title": "Может изменять"},
            {"code": "VIEWER", "title": "Только просмотр"},
        ],
    }


@router.get("", response_model=None)
async def list_employees(
    ctx: StoreContext = Depends(require_store_owner),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(User, UserStore)
        .join(UserStore, User.id == UserStore.user_id)
        .where(UserStore.store_id == ctx.store_id, UserStore.role != "OWNER")
    )
    employees = [_row_to_out(user, us) for user, us in res.all()]

    from backend.billing.limits import employee_seat_limit

    # Синхронная функция с sync-сессией БД — как и остальные вызовы billing.*
    # из async-роутеров, уводим в поток, чтобы не держать event loop.
    limit = await asyncio.to_thread(employee_seat_limit, ctx.store_id)
    return {
        "employees": employees,
        "used": len(employees),
        "limit": limit,  # None = безлимит (DEV/ГигаПредприятие)
    }


@router.post("", response_model=None)
async def create_employee(
    req: EmployeeCreateRequest,
    ctx: StoreContext = Depends(require_store_owner),
    db: AsyncSession = Depends(get_db),
):
    """Заводит сотрудника: новый аккаунт с временным паролем, либо выдаёт
    доступ уже существующему пользователю платформы (тогда пароль его
    собственный и мы его не трогаем)."""
    preset = PRESETS_BY_CODE.get(req.preset)
    if not preset:
        raise HTTPException(status_code=400, detail=f"Неизвестная должность: {req.preset}")

    from backend.billing.limits import employee_seat_limit

    limit = await asyncio.to_thread(employee_seat_limit, ctx.store_id)
    if limit is not None:
        used = await _count_employees(db, ctx.store_id)
        if used >= limit:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"На вашем тарифе доступно {limit} сотрудник(ов), уже заведено {used}. "
                    "Повысьте тариф, чтобы добавить ещё."
                ),
            )

    email = req.email.lower().strip()
    sections = sanitize_sections(req.sections if req.sections is not None else preset.sections)

    res = await db.execute(select(User).where(User.email == email))
    user = res.scalar_one_or_none()
    temp_password: Optional[str] = None

    if user:
        existing = await db.execute(
            select(UserStore).where(UserStore.user_id == user.id, UserStore.store_id == ctx.store_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Этот сотрудник уже добавлен в магазин")
        if user.is_telegram_only:
            raise HTTPException(
                status_code=409,
                detail="Этот аккаунт создан через Telegram и не имеет входа в веб-кабинет. Заведите сотрудника с другой почтой.",
            )
    else:
        temp_password = _generate_password()
        user = User(
            email=email,
            password_hash=hash_password(temp_password),
            full_name=req.full_name,
            is_active=True,
            is_verified=True,  # аккаунт заведён владельцем вручную, почту не подтверждаем
            must_change_password=True,
        )
        db.add(user)
        await db.flush()

    db.add(
        UserStore(
            user_id=user.id,
            store_id=ctx.store_id,
            role=preset.role,
            allowed_sections=json.dumps(sections),
            job_preset=preset.code,
            job_title=req.job_title or preset.title,
            created_at=datetime.utcnow(),
        )
    )
    await db.commit()
    await db.refresh(user)

    res = await db.execute(
        select(UserStore).where(UserStore.user_id == user.id, UserStore.store_id == ctx.store_id)
    )
    us = res.scalar_one()
    logger.info("Сотрудник %s добавлен в магазин %s (пресет %s)", email, ctx.store_id, preset.code)
    return {
        "employee": _row_to_out(user, us),
        # Показывается владельцу один раз и больше нигде не хранится в
        # открытом виде — забыли переписать, жмите «Сбросить пароль».
        "temp_password": temp_password,
    }


@router.patch("/{user_id}", response_model=None)
async def update_employee(
    user_id: str,
    req: EmployeeUpdateRequest,
    ctx: StoreContext = Depends(require_store_owner),
    db: AsyncSession = Depends(get_db),
):
    user, us = await _load_target(db, ctx.store_id, user_id)

    if req.preset is not None:
        preset = PRESETS_BY_CODE.get(req.preset)
        if not preset:
            raise HTTPException(status_code=400, detail=f"Неизвестная должность: {req.preset}")
        us.job_preset = preset.code
        us.role = preset.role
        # Пресет задаёт и роль, и разделы. Если в этом же запросе пришли
        # свои sections — они выигрывают (владелец сменил должность и тут же
        # подправил галочки).
        us.allowed_sections = json.dumps(sanitize_sections(preset.sections))

    if req.role is not None:
        if req.role not in EMPLOYEE_ROLES:
            raise HTTPException(status_code=400, detail="Сотруднику доступны роли MANAGER или VIEWER")
        us.role = req.role

    if req.sections is not None:
        us.allowed_sections = json.dumps(sanitize_sections(req.sections))
        if req.preset is None:
            # Ушли от пресета вручную — метка должности перестаёт быть
            # правдой, но название («Сборщик») пусть остаётся, оно про
            # человека, а не про набор галочек.
            us.job_preset = None

    if req.job_title is not None:
        us.job_title = req.job_title or None

    if req.is_active is not None:
        # Отстранение без удаления: доступ закрыт, место в тарифе занято,
        # права и история сохранены — типичный «уехал/в отпуске/разбираемся».
        user.is_active = req.is_active

    await db.commit()
    await db.refresh(us)
    await db.refresh(user)
    return {"employee": _row_to_out(user, us)}


@router.post("/{user_id}/reset-password", response_model=None)
async def reset_employee_password(
    user_id: str,
    ctx: StoreContext = Depends(require_store_owner),
    db: AsyncSession = Depends(get_db),
):
    user, us = await _load_target(db, ctx.store_id, user_id)
    if user.is_telegram_only:
        raise HTTPException(status_code=400, detail="У Telegram-сотрудника нет пароля от веб-кабинета")

    other_stores = await db.execute(
        select(func.count(UserStore.store_id)).where(
            UserStore.user_id == user.id, UserStore.store_id != ctx.store_id
        )
    )
    if (other_stores.scalar() or 0) > 0:
        # Аккаунт работает и в другом магазине (в т.ч. может быть владельцем
        # своего) — сброс пароля отсюда выкинул бы человека оттуда. Такой
        # доступ владелец этого магазина не покупал.
        raise HTTPException(
            status_code=403,
            detail="Этот аккаунт используется и в других магазинах — пароль сбрасывает только его владелец.",
        )

    new_password = _generate_password()
    user.password_hash = hash_password(new_password)
    user.must_change_password = True
    await db.commit()
    logger.info("Пароль сотрудника %s сброшен владельцем магазина %s", user.email, ctx.store_id)
    return {"temp_password": new_password}


@router.delete("/{user_id}", response_model=None)
async def remove_employee(
    user_id: str,
    ctx: StoreContext = Depends(require_store_owner),
    db: AsyncSession = Depends(get_db),
):
    user, us = await _load_target(db, ctx.store_id, user_id)
    await db.delete(us)
    await db.flush()

    remaining = await db.execute(
        select(func.count(UserStore.store_id)).where(UserStore.user_id == user.id)
    )
    orphaned = (remaining.scalar() or 0) == 0
    if orphaned and user.must_change_password:
        # Аккаунт был заведён этим владельцем, ни в один магазин больше не
        # входит и ни разу не менял выданный пароль — то есть человек им так
        # и не воспользовался. Оставлять висеть незачем: занятая почта потом
        # не даст завести его же заново. Аккаунт, который УЖЕ пользовались
        # (пароль сменён), не удаляем — он мог оказаться личным.
        await db.delete(user)
    await db.commit()
    logger.info("Сотрудник %s отвязан от магазина %s (аккаунт удалён: %s)", user.email, ctx.store_id, orphaned and user.must_change_password)
    return {"success": True}
