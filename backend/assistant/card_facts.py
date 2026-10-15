"""Store-scoped projection of reviewed public facts into the draft contract."""
import json
import logging

from sqlalchemy import select

from backend import state_helpers
from backend.db.models import StoreCardFactDB

logger = logging.getLogger(__name__)


def load_reviewed_cards(store_id: str) -> list[dict]:
    try:
        session = state_helpers._get_sync_db_session()
        if session is None:
            logger.warning("Store card facts could not be read")
            return []
        with session:
            rows = session.scalars(select(StoreCardFactDB).where(
                StoreCardFactDB.store_id == store_id,
                StoreCardFactDB.reviewed.is_(True),
            ).order_by(StoreCardFactDB.sku)).all()
            cards = []
            for row in rows:
                pairs = json.loads(row.features or "[]")
                features = {f"f{i}": value for i, (name, value) in enumerate(pairs)}
                meta = {f"f{i}": {"name": name} for i, (name, value) in enumerate(pairs)}
                cards.append({"id": row.id, "status": "approved", "raw_title": row.name,
                              "draft_payload": {"sku": row.sku, "category_name": row.category,
                                                "brand_name": row.brand, "features": features,
                                                "feature_meta": meta, "description": row.description}})
            return cards
    except Exception:
        # SQL errors may contain private parameters. Never log exception text.
        logger.warning("Store card facts could not be read", exc_info=False)
        return []
