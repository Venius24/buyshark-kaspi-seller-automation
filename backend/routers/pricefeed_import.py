"""
Импорт настроек демпинга (min/max цена, шаг, алгоритм) из конкурентного
сервиса PriceFeed.kz — см. REAL_IMPORTANT/импорт_pricefeed/PRICEFEED_IMPORT.md
и план в HANDOFF.md.

Выключено по умолчанию (PRICEFEED_IMPORT_ENABLED=1 в .env, чтобы включить) —
селекторы формы входа PriceFeed хрупкие (обычный сторонний UI, может
поменяться без предупреждения), а сломанный логин просто жжёт чужую квоту
запросов (у PriceFeed ~250/30мин на аккаунт).

Данные между /preview и /apply живут в серверном in-memory TTL-кэше
(_PENDING), НЕ в БД — повторная выгрузка на apply удвоила бы расход чужой
квоты и заставила бы ждать ~минуту второй раз. Это безопасно ровно потому,
что прод крутится как uvicorn --workers 1 (deploy/kaspi-saas.service) — при
переходе на несколько воркеров это нужно переносить в БД/Redis.

Пароль от PriceFeed — request-scoped Pydantic-поле, используется только
внутри вызова fetch_pricefeed_settings и НИКОГДА не попадает ни в _PENDING,
ни в БД, ни в логи.
"""

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from backend.security.store_context import StoreContext, get_store_context_billed
from backend.security.store_roles import require_store_manager_billed, store_section_guard
from backend.services import pricefeed_client
from backend.services.playwright_worker import worker as pw_worker
from backend.services import pricefeed_mapping
from backend.state_helpers import _load_dumping_configs, _save_dumping_configs, _load_products

logger = logging.getLogger(__name__)

# billed=True: оба эндпоинта здесь (preview/apply) — мутирующие POST'ы, уже
# сами резолвятся через get_store_context_billed, а не get_store_context.
# Гейт раздела ходит по той же billed-версии, а не заводит второй,
# небилледный резолв стора поверх (см. store_section_guard).
router = APIRouter(
    prefix="/api/dumping/pricefeed",
    tags=["PriceFeed Import"],
    dependencies=[Depends(store_section_guard("dumping", billed=True))],
)

_PENDING_TTL_SECONDS = 900
_IMPORT_COOLDOWN_SECONDS = 300
_MAX_CONCURRENT_IMPORTS = int(os.getenv("PRICEFEED_MAX_CONCURRENT", "2"))

_concurrency_semaphore = threading.Semaphore(_MAX_CONCURRENT_IMPORTS)


def _pricefeed_import_enabled() -> bool:
    return os.getenv("PRICEFEED_IMPORT_ENABLED", "0") == "1"


def _guard_enabled():
    if not _pricefeed_import_enabled():
        raise HTTPException(
            status_code=503,
            detail="Импорт из PriceFeed пока в разработке — обратитесь к нам, чтобы включить.",
        )


@dataclass
class _PendingImport:
    store_id: str
    created_at: float
    merchant: dict
    rows: dict  # {sku: config_dict} — уже смаппленные, PriceFeed-агностичные
    report: dict


_PENDING: dict = {}
_PENDING_LOCK = threading.Lock()
_LAST_IMPORT_AT: dict = {}  # store_id -> monotonic ts последнего /preview


def _evict_stale_imports():
    now = time.monotonic()
    with _PENDING_LOCK:
        stale = [k for k, v in _PENDING.items() if now - v.created_at > _PENDING_TTL_SECONDS]
        for k in stale:
            del _PENDING[k]


class PriceFeedPreviewRequest(BaseModel):
    phone: str
    password: str
    merchant_id: Optional[str] = None


class PriceFeedApplyRequest(BaseModel):
    import_id: str
    overwrite: bool = False
    # Импорт по умолчанию СПЯЩИЙ: конфиги ложатся выключенными, демпинг по ним
    # не стартует. Иначе один клик поднимает сотни товаров разом — а глобального
    # рубильника у демпинга нет (backend/dumper/__init__.py крутит всё, у чего
    # enabled=True), то есть откатить можно только вручную по одному. Плюс
    # квота Kaspi на запись офферов — 250 за 30 минут на весь магазин, и залп
    # выравнивания цен по всему ассортименту в неё не влезает.
    activate: bool = False


def _check_cooldown(store_id: str):
    now = time.monotonic()
    last = _LAST_IMPORT_AT.get(store_id)
    if last is not None and (now - last) < _IMPORT_COOLDOWN_SECONDS:
        wait_left = int(_IMPORT_COOLDOWN_SECONDS - (now - last))
        raise HTTPException(
            status_code=429,
            detail=f"Импорт из PriceFeed уже запускался недавно. Повторите через {wait_left} сек.",
        )


def _run_fetch(phone: str, password: str, merchant_id: Optional[str]):
    return pw_worker.run_in_browser_pool(
        pricefeed_client.fetch_pricefeed_settings, phone, password, merchant_id
    )


@router.post("/preview")
async def preview_pricefeed_import(
    req: PriceFeedPreviewRequest,
    ctx: StoreContext = Depends(require_store_manager_billed),
):
    _guard_enabled()
    _evict_stale_imports()
    _check_cooldown(ctx.store_id)

    if not _concurrency_semaphore.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail="Сервис импорта сейчас занят другими магазинами, попробуйте через минуту.",
        )
    try:
        try:
            result = await run_in_threadpool(_run_fetch, req.phone, req.password, req.merchant_id)
        except pricefeed_client.PriceFeedAuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        except pricefeed_client.PriceFeedRateLimited as e:
            raise HTTPException(status_code=429, detail=str(e))
        except pricefeed_client.PriceFeedNoMerchants as e:
            raise HTTPException(status_code=400, detail=str(e))
        except pricefeed_client.PriceFeedError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except Exception:
            logger.exception("Ошибка при выгрузке из PriceFeed (store_id=%s)", ctx.store_id)
            raise HTTPException(
                status_code=504,
                detail="Не удалось завершить выгрузку из PriceFeed (таймаут или сбой). Попробуйте ещё раз.",
            )
    finally:
        _concurrency_semaphore.release()

    _LAST_IMPORT_AT[ctx.store_id] = time.monotonic()

    if result.merchant is None:
        # Несколько магазинов у аккаунта — просим фронт уточнить выбор
        # (без import_id, повторный /preview придёт уже с merchant_id).
        return {
            "status": "choose_merchant",
            "merchants": [{"id": m.id, "name": m.name} for m in result.merchants],
        }

    products = _load_products(store_id=ctx.store_id)
    existing_configs = _load_dumping_configs(store_id=ctx.store_id)
    match_report = pricefeed_mapping.match_items(result.items, products, existing_configs)

    import_id = str(uuid.uuid4())
    with _PENDING_LOCK:
        _PENDING[import_id] = _PendingImport(
            store_id=ctx.store_id,
            created_at=time.monotonic(),
            merchant={"id": result.merchant.id, "name": result.merchant.name},
            rows=match_report.rows,
            report={
                "skipped": match_report.skipped,
                "warnings": match_report.warnings,
            },
        )

    sample = [
        {
            "sku": sku,
            "title": cfg.get("_source_title"),
            "min_price": cfg["min_price"],
            "max_price": cfg["max_price"],
            "dump_step": cfg["dump_step"],
            "mode": cfg["mode"],
            "already_configured": sku in match_report.already_configured,
        }
        for sku, cfg in list(match_report.rows.items())[:20]
    ]

    logger.info(
        "PriceFeed preview store_id=%s merchant=%s total=%d importable=%d pages=%d truncated=%s",
        ctx.store_id, result.merchant.name, len(result.items), len(match_report.rows),
        result.pages_fetched, result.truncated,
    )

    return {
        "status": "ready",
        "import_id": import_id,
        "expires_in_seconds": _PENDING_TTL_SECONDS,
        "merchant": {"id": result.merchant.id, "name": result.merchant.name},
        "total_fetched": len(result.items),
        "importable": len(match_report.rows),
        "matched": len(match_report.rows),
        "unmatched": len(match_report.unmatched),
        "unmatched_sample": match_report.unmatched[:10],
        "already_configured": len(match_report.already_configured),
        "skipped": match_report.skipped,
        "warnings": match_report.warnings,
        "sample": sample,
        "truncated": result.truncated,
    }


@router.post("/apply")
async def apply_pricefeed_import(
    req: PriceFeedApplyRequest,
    ctx: StoreContext = Depends(require_store_manager_billed),
):
    _guard_enabled()
    _evict_stale_imports()

    with _PENDING_LOCK:
        entry = _PENDING.get(req.import_id)

    if entry is None:
        raise HTTPException(status_code=410, detail="Данные импорта устарели, запустите импорт заново.")
    if entry.store_id != ctx.store_id:
        # Не подтверждаем существование чужого import_id — 404, а не 403.
        raise HTTPException(status_code=404, detail="Импорт не найден.")

    existing_configs = _load_dumping_configs(store_id=ctx.store_id)

    to_write = {}
    created = 0
    updated = 0
    skipped_existing = 0

    for sku, new_config in entry.rows.items():
        already = sku in existing_configs
        if already and not req.overwrite:
            skipped_existing += 1
            continue
        if already:
            merged = pricefeed_mapping.merge_with_existing(new_config, existing_configs[sku])
            if not req.activate:
                # Не трогаем чужой рубильник: товар, который уже демпинговался,
                # продолжает демпинговаться, выключенный остаётся выключенным.
                merged["enabled"] = bool(existing_configs[sku].get("enabled", False))
            to_write[sku] = merged
            updated += 1
        else:
            cfg = dict(new_config)
            cfg.pop("_source_title", None)
            cfg.pop("sku", None)
            if not req.activate:
                cfg["enabled"] = False
            to_write[sku] = cfg
            created += 1

    if to_write:
        _save_dumping_configs(to_write, store_id=ctx.store_id)

    with _PENDING_LOCK:
        _PENDING.pop(req.import_id, None)

    logger.info(
        "PriceFeed apply store_id=%s created=%d updated=%d skipped_existing=%d activate=%s",
        ctx.store_id, created, updated, skipped_existing, req.activate,
    )

    return {
        "success": True,
        "created": created,
        "updated": updated,
        "skipped_existing": skipped_existing,
        "total": created + updated,
        "activated": req.activate,
    }
