"""Проверка прав внутри магазина — ортогонально platform_roles.py
(User.is_superadmin, платформенный доступ). Здесь ДВЕ независимые оси, обе
живут в user_stores и обе кладутся в StoreContext резолвером
get_store_context (эти зависимости ничего не резолвят сами, только читают
уже готовый контекст):

  * `role` — ГЛУБИНА прав, линейный ранг VIEWER < MANAGER < OWNER
    (`require_store_role`);
  * `sections` — ШИРИНА прав, набор разделов кабинета
    (`require_section`, справочник — backend/security/sections.py).

Почему не один enum ролей: сборщик и чат-менеджер имеют ОДИНАКОВУЮ глубину
прав (оба меняют данные), но разную ширину (упаковка/печать против чатов).
На линейный ранг это не ложится.
"""
from fastapi import Depends, HTTPException, Request, status

from backend.security.sections import SECTIONS
from backend.security.store_context import StoreContext, get_store_context, get_store_context_billed

ROLE_RANK = {"VIEWER": 0, "MANAGER": 1, "OWNER": 2}

_SECTION_TITLES = {s.key: s.title for s in SECTIONS}


def require_store_role(min_role: str, billed: bool = False):
    base_dep = get_store_context_billed if billed else get_store_context

    async def dep(ctx: StoreContext = Depends(base_dep)) -> StoreContext:
        if ROLE_RANK.get(ctx.role, 0) < ROLE_RANK[min_role]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для этого действия")
        return ctx

    return dep


def require_section(section: str, min_role: str = "VIEWER", billed: bool = False):
    """Раздел + минимальный ранг одной зависимостью.

    `min_role` тут не дублирует require_store_role, а закрывает вторую
    половину вопроса: «этому сотруднику вообще открыт раздел» и «он может в
    нём МЕНЯТЬ» — разные вещи. Наблюдателю (VIEWER) упаковка видна, но
    кнопка «Собрать» для него закрыта, и закрыта она здесь, на сервере, а не
    тем, что фронтенд её не нарисовал.
    """
    if section not in _SECTION_TITLES:
        raise ValueError(f"Неизвестный раздел кабинета: {section}")
    base_dep = get_store_context_billed if billed else get_store_context

    async def dep(ctx: StoreContext = Depends(base_dep)) -> StoreContext:
        # sections=None — system-internal контекст (фоновый воркер, публичный
        # 1С-API), у него нет запрашивающего пользователя и ограничивать
        # нечего. Такие контексты через HTTP-зависимости не приходят.
        if ctx.sections is not None and section not in ctx.sections:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Раздел «{_SECTION_TITLES[section]}» недоступен для вашей роли в этом магазине",
            )
        if ROLE_RANK.get(ctx.role, 0) < ROLE_RANK[min_role]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для этого действия")
        return ctx

    return dep


require_store_manager = require_store_role("MANAGER")
require_store_manager_billed = require_store_role("MANAGER", billed=True)
require_store_owner = require_store_role("OWNER")


def store_section_guard(section: str, billed: bool = False):
    """Роутер-уровневая охрана раздела: вешается один раз на APIRouter, а не
    по одной строке на каждый эндпоинт.

    Делает две вещи:
      * раздел закрыт для роли — 403 на ЛЮБОЙ метод;
      * запрос меняющий (не GET/HEAD/OPTIONS) — нужен ранг MANAGER, то есть
        VIEWER («Наблюдатель») действительно только смотрит.

    Стоит ровно ноль лишних запросов к БД: внутри тот же Depends(get_store_context)
    (или get_store_context_billed — см. ниже), что и у самих эндпоинтов, а
    FastAPI кэширует результат зависимости в пределах запроса по её callable —
    резолв магазина происходит один раз, а не дважды.

    `billed=False` (по умолчанию) — тарифная блокировка выборочно висит на
    конкретных action-ручках, и поднимать её на весь роутер значило бы
    закрыть заблокированному магазину в том числе чтение, а он должен видеть
    кабинет и платить (см. get_store_context_billed). Передавайте
    `billed=True` только если у роутера НЕТ ни одного GET-эндпоинта — тогда
    гейт использует тот же get_store_context_billed, что и сами эндпоинты
    (иначе получится два независимых резолва стора на один запрос: один
    небилледный тут, другой билледный внутри ручки — а FastAPI не переиспользует
    результат одной зависимости для другой)."""
    if section not in _SECTION_TITLES:
        raise ValueError(f"Неизвестный раздел кабинета: {section}")
    base_dep = get_store_context_billed if billed else get_store_context

    async def dep(request: Request, ctx: StoreContext = Depends(base_dep)) -> StoreContext:
        if ctx.sections is not None and section not in ctx.sections:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Раздел «{_SECTION_TITLES[section]}» недоступен для вашей роли в этом магазине",
            )
        if request.method not in ("GET", "HEAD", "OPTIONS") and ROLE_RANK.get(ctx.role, 0) < ROLE_RANK["MANAGER"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Ваша роль в этом магазине — только просмотр",
            )
        return ctx

    return dep
