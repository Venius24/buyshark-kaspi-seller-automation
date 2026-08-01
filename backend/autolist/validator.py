"""
Micro-check и оркестрация черновиков для фичи «Автодобавление товара»:
склеивает backend.autolist.extractor (AI-распознавание) и
backend.autolist.category_mapper (Kaspi API) через таблицу
draft_products (backend/db/models.py::DraftProductDB).

Ключевое архитектурное решение (см. HANDOFF.md 3.17, зафиксировано после
живого тестирования gpt-4o-mini): AI регулярно ошибается даже на формально
"заполненных" полях (не только на пустых) — поэтому этот модуль НИКОГДА не
отправляет черновик в Kaspi автоматически. submit_draft() требует явного
approved=True, выставляемого только человеком в review UI после того как он
открыл черновик и посмотрел на КАЖДОЕ поле, не только на missing_mandatory.
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PHOTOS_DIR = os.path.join(BASE_DIR, "states", "autolist_photos")


class DraftNotFoundError(Exception):
    pass


class DraftNotApprovedError(Exception):
    pass


def _session():
    from backend.state_helpers import _get_sync_db_session
    return _get_sync_db_session()


def _row_to_dict(row) -> dict:
    return {
        "id": row.id,
        "store_id": row.store_id,
        "photo_path": row.photo_path,
        "raw_title": row.raw_title,
        "status": row.status,
        "ai_raw": json.loads(row.ai_raw or "{}"),
        "draft_payload": json.loads(row.draft_payload or "{}"),
        "missing_mandatory": json.loads(row.missing_mandatory or "[]"),
        "kaspi_result": json.loads(row.kaspi_result) if row.kaspi_result else None,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _save_uploaded_photo(store_id: str, source_path: str) -> str:
    """Копирует загруженное фото в states/autolist_photos/<store_id>/ под
    уникальным именем — черновик должен пережить удаление исходного
    временного файла загрузки (см. роутер, который вызывает create_draft)."""
    import shutil
    import uuid

    store_dir = os.path.join(PHOTOS_DIR, store_id)
    os.makedirs(store_dir, exist_ok=True)
    ext = os.path.splitext(source_path)[1] or ".jpg"
    dest_path = os.path.join(store_dir, f"{uuid.uuid4()}{ext}")
    shutil.copyfile(source_path, dest_path)
    return dest_path


def compute_missing_mandatory(draft_payload: dict) -> list[str]:
    """Пересчитывает список незаполненных обязательных полей ПО ТЕКУЩЕМУ
    состоянию draft_payload (не по исходному ai_raw) — используется и сразу
    после AI-заполнения, и после каждого ручного редактирования в review UI."""
    feature_meta = draft_payload.get("feature_meta", {})
    features = draft_payload.get("features", {})
    missing = []
    for code, meta in feature_meta.items():
        if not meta.get("mandatory"):
            continue
        val = features.get(code)
        if meta.get("multiValued"):
            if not val:
                missing.append(code)
        elif val is None or val == "":
            missing.append(code)

    if not draft_payload.get("sku"):
        missing.append("__sku__")
    if not draft_payload.get("category_code"):
        missing.append("__category__")
    if not draft_payload.get("price") or draft_payload.get("price", 0) <= 0:
        missing.append("__price__")
    if draft_payload.get("stock") is None or draft_payload.get("stock", -1) < 0:
        missing.append("__stock__")
    return missing


def create_draft(store_id: str, photo_path: str, raw_title: str, token: str) -> dict:
    """Полный первый проход: category_mapper.search_existing_products (для
    показа кандидатов "может это уже есть?") + extractor.guess_category →
    category_mapper.search_categories → category_mapper.fetch_category_schema
    → extractor.fill_attributes → сохранённый DraftProductDB со
    status='needs_review'.

    Синхронная функция (category_mapper — sync/requests), но extractor —
    async/httpx (внешний OpenAI API) — вызывающий роутер должен звать
    extractor-часть отдельно и передать сюда уже готовый результат, ЛИБО
    (проще для FastAPI async-роута) обернуть этот вызов в
    run_in_threadpool/asyncio.to_thread. См. TODO в backend/routers/autolist.py
    (роутер ещё не написан)."""
    raise NotImplementedError(
        "create_draft смешивает sync (category_mapper) и async (extractor) вызовы — "
        "оркестрация должна жить в async-роутере (backend/routers/autolist.py, ещё не "
        "написан), который сам вызовет extractor.guess_category/fill_attributes (await) "
        "и category_mapper.* (sync, через asyncio.to_thread), а затем save_draft() ниже "
        "для персистентности. Эта функция-заглушка документирует ожидаемый контракт, "
        "но не вызывается напрямую — см. save_draft/update_draft/submit_draft, которые "
        "уже рабочие и не завязаны на конкретный event loop."
    )


def save_draft(store_id: str, photo_path: str, raw_title: str, ai_raw: dict, draft_payload: dict) -> dict:
    """Сохраняет новый черновик после того как вызывающий код (роутер) уже
    прогнал extractor+category_mapper и собрал draft_payload. photo_path —
    путь к УЖЕ временно загруженному файлу, будет скопирован в постоянное
    хранилище states/autolist_photos/."""
    from backend.db.models import DraftProductDB

    stored_photo_path = _save_uploaded_photo(store_id, photo_path)
    missing = compute_missing_mandatory(draft_payload)

    session = _session()
    if not session:
        raise RuntimeError("Нет доступа к БД (_get_sync_db_session вернул None)")
    try:
        row = DraftProductDB(
            store_id=store_id,
            photo_path=stored_photo_path,
            raw_title=raw_title,
            status="needs_review",
            ai_raw=json.dumps(ai_raw, ensure_ascii=False),
            draft_payload=json.dumps(draft_payload, ensure_ascii=False),
            missing_mandatory=json.dumps(missing, ensure_ascii=False),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _row_to_dict(row)
    finally:
        session.close()


def get_draft(draft_id: str, store_id: str) -> dict:
    from backend.db.models import DraftProductDB

    session = _session()
    if not session:
        raise RuntimeError("Нет доступа к БД")
    try:
        row = session.get(DraftProductDB, draft_id)
        if not row or row.store_id != store_id:
            raise DraftNotFoundError(draft_id)
        return _row_to_dict(row)
    finally:
        session.close()


def list_drafts(store_id: str, status: Optional[str] = None) -> list[dict]:
    from sqlalchemy import select
    from backend.db.models import DraftProductDB

    session = _session()
    if not session:
        return []
    try:
        stmt = select(DraftProductDB).where(DraftProductDB.store_id == store_id)
        if status:
            stmt = stmt.where(DraftProductDB.status == status)
        stmt = stmt.order_by(DraftProductDB.created_at.desc())
        rows = session.scalars(stmt).all()
        return [_row_to_dict(r) for r in rows]
    finally:
        session.close()


def delete_draft(draft_id: str, store_id: str) -> None:
    """Отмена черновика — удаляет и ряд в БД, и скопированный файл фото."""
    from backend.db.models import DraftProductDB

    session = _session()
    if not session:
        raise RuntimeError("Нет доступа к БД")
    try:
        row = session.get(DraftProductDB, draft_id)
        if not row or row.store_id != store_id:
            raise DraftNotFoundError(draft_id)
        photo_path = row.photo_path
        session.delete(row)
        session.commit()
        if photo_path and os.path.exists(photo_path):
            try:
                os.remove(photo_path)
            except OSError:
                pass
    finally:
        session.close()


def update_draft(draft_id: str, store_id: str, draft_payload_patch: dict) -> dict:
    """Ручное редактирование в review UI — сливает patch поверх текущего
    draft_payload (не заменяет целиком, чтобы фронтенд мог прислать только
    изменённые поля), пересчитывает missing_mandatory, статус остаётся
    'needs_review' (approve — отдельный явный шаг, см. approve_draft)."""
    from backend.db.models import DraftProductDB

    session = _session()
    if not session:
        raise RuntimeError("Нет доступа к БД")
    try:
        row = session.get(DraftProductDB, draft_id)
        if not row or row.store_id != store_id:
            raise DraftNotFoundError(draft_id)

        current = json.loads(row.draft_payload or "{}")
        if "features" in draft_payload_patch:
            current.setdefault("features", {}).update(draft_payload_patch.pop("features"))
        current.update(draft_payload_patch)

        row.draft_payload = json.dumps(current, ensure_ascii=False)
        row.missing_mandatory = json.dumps(compute_missing_mandatory(current), ensure_ascii=False)
        row.status = "needs_review"
        row.updated_at = datetime.utcnow()
        session.commit()
        session.refresh(row)
        return _row_to_dict(row)
    finally:
        session.close()


def approve_draft(draft_id: str, store_id: str) -> dict:
    """Явное подтверждение человеком — ТОЛЬКО после этого draft можно
    отправить в submit_draft(). Блокируется, если ещё остались обязательные
    незаполненные поля (пересчитывает на актуальном draft_payload, не
    доверяет закэшированному missing_mandatory на случай гонки)."""
    from backend.db.models import DraftProductDB

    session = _session()
    if not session:
        raise RuntimeError("Нет доступа к БД")
    try:
        row = session.get(DraftProductDB, draft_id)
        if not row or row.store_id != store_id:
            raise DraftNotFoundError(draft_id)

        payload = json.loads(row.draft_payload or "{}")
        missing = compute_missing_mandatory(payload)
        if missing:
            row.missing_mandatory = json.dumps(missing, ensure_ascii=False)
            session.commit()
            raise DraftNotApprovedError(f"Незаполненные обязательные поля: {missing}")

        row.status = "approved"
        row.updated_at = datetime.utcnow()
        session.commit()
        session.refresh(row)
        return _row_to_dict(row)
    finally:
        session.close()


def submit_draft(draft_id: str, store_id: str, token: str) -> dict:
    """Финальная отправка в Kaspi (category_mapper.submit_new_product) —
    требует status=='approved' (см. approve_draft). Дубликат SKU
    (category_mapper.DuplicateSkuError) НЕ считается провалом черновика —
    статус откатывается в 'needs_review' с понятным error_message, чтобы
    пользователь мог поменять артикул и подтвердить заново, не начиная с
    нуля (см. живой прецедент в HANDOFF 3.17 — ровно так это и произошло
    руками во время реверс-инжиниринга)."""
    from backend.db.models import DraftProductDB
    from backend.autolist import category_mapper as cm

    session = _session()
    if not session:
        raise RuntimeError("Нет доступа к БД")
    try:
        row = session.get(DraftProductDB, draft_id)
        if not row or row.store_id != store_id:
            raise DraftNotFoundError(draft_id)
        if row.status != "approved":
            raise DraftNotApprovedError(f"Черновик в статусе {row.status!r}, а не 'approved' — сначала approve_draft()")

        payload = json.loads(row.draft_payload or "{}")
        row.status = "submitting"
        session.commit()

        features_payload = cm.build_features_payload(payload.get("features", {}), payload.get("feature_meta", {}))

        try:
            result = cm.submit_new_product(
                sku=payload["sku"],
                category_code=payload["category_code"],
                category_name=payload.get("category_name", ""),
                brand_name=payload.get("brand_name"),
                photo_path=row.photo_path,
                features_payload=features_payload,
                description=payload.get("description", ""),
                classifications=payload.get("classifications", []),
                token=token,
                store_id=store_id,
            )
            row.status = "submitted"
            row.kaspi_result = json.dumps(result, ensure_ascii=False)
            row.error_message = None
        except cm.DuplicateSkuError as e:
            row.status = "needs_review"
            row.error_message = f"Артикул {e.sku!r} уже используется в Kaspi: {e.message}"
        except Exception as e:
            row.status = "error"
            row.error_message = str(e)
            logger.error(f"submit_draft({draft_id}) провалился: {e}")

        row.updated_at = datetime.utcnow()
        session.commit()
        session.refresh(row)
        return _row_to_dict(row)
    finally:
        session.close()
