"""offer.buyshark.kz — персональные КП для лидов, по образцу lead_service.

Запуск локально: python -m uvicorn offer_service.main:app --reload --port 8030
"""

import os
import secrets
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, model_validator

from offer_service import db, tariffs

app = FastAPI(title="offer.buyshark.kz")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

def _fmt_tenge(value) -> str:
    if value is None:
        return "по договору"
    return f"{value:,.0f}".replace(",", " ") + " ₸"


_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.filters["tg"] = _fmt_tenge

_ALMATY = ZoneInfo("Asia/Almaty")
_WHATSAPP_PHONE = "77009990011"

db.init_db()


# ---------------------------------------------------------------------------
# Админ-токен — та же схема, что в lead_service/main.py: ключ не хранится в
# коде, читается из .env в корне репозитория (юнит без EnvironmentFile).
# ---------------------------------------------------------------------------

_ENV_FILE = Path(__file__).parent.parent / ".env"
_env_cache: dict = {"mtime": None, "token": ""}


def _admin_token() -> str:
    from_env = os.environ.get("OFFER_ADMIN_TOKEN")
    if from_env:
        return from_env
    try:
        mtime = _ENV_FILE.stat().st_mtime
    except OSError:
        return ""
    if _env_cache["mtime"] != mtime:
        token = ""
        try:
            for line in _ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("OFFER_ADMIN_TOKEN="):
                    token = line.split("=", 1)[1].strip().strip("'\"")
                    break
        except OSError:
            token = ""
        _env_cache.update(mtime=mtime, token=token)
    return _env_cache["token"]


def _require_admin(k: str | None):
    token = _admin_token()
    if not token or not k or not secrets.compare_digest(k, token):
        raise HTTPException(status_code=404, detail="Not Found")


# ---------------------------------------------------------------------------
# Вычисление контента КП из сохранённой записи лида
# ---------------------------------------------------------------------------


def _wa_link(client_name: str, tier_name: str) -> str:
    text = (
        f"Здравствуйте, я из {client_name}. "
        f"Получил КП по тарифу «{tier_name}», хочу подключить магазин.\n\n"
        f"[КП {client_name}]"
    )
    return f"https://wa.me/{_WHATSAPP_PHONE}?text={quote(text)}"


def _pl_tovar(n: int) -> str:
    n_mod100 = n % 100
    if 11 <= n_mod100 <= 14:
        return "товаров"
    n_mod10 = n % 10
    if n_mod10 == 1:
        return "товар"
    if 2 <= n_mod10 <= 4:
        return "товара"
    return "товаров"


def _recommendation_text(product_count: int | None, tier: tariffs.Tier, prev: tariffs.Tier | None) -> str:
    name = tier.name
    if product_count is None:
        return f"Подбираем тариф «{name}» как разумный старт — уточним точный размер каталога при подключении."
    word = _pl_tovar(product_count)
    if tier.key == "giga":
        return (
            f"У вас {product_count} {word} — это больше потолка «Хищник+» (2 000), "
            f"поэтому считаем цену индивидуально под ваш объём и SLA."
        )
    if prev is None or product_count <= prev.limit:
        return (
            f"У вас {product_count} {word} — укладывается в тариф «{name}» "
            f"(до {tier.limit}) с запасом на рост."
        )
    headroom = tier.limit - product_count
    return (
        f"У вас {product_count} {word} — это уже больше, чем помещается в тариф «{prev.name}» "
        f"({prev.limit}), но заметно меньше потолка «{name}» ({tier.limit}). Он и подходит: "
        f"{headroom} свободных мест хватит, даже если каталог за год-два подрастёт, и не придётся "
        f"среди сезона переходить на тариф дороже."
    )


def _build_context(request: Request, offer: dict) -> dict:
    product_count = offer.get("product_count")
    override = offer.get("tier_override")
    tier = tariffs.tier_by_key(override) if override else tariffs.pick_tier(product_count)
    tier = tier or tariffs.pick_tier(product_count)
    prev = tariffs.prev_tier(tier)

    discount_pct = offer.get("discount_pct") or tariffs.DISCOUNT_PCT_DEFAULT
    fixed_price_offer = (offer.get("first_month_price") is not None
                         and offer.get("monthly_price") is not None)
    monthly_price = offer["monthly_price"] if fixed_price_offer else tier.price
    discounted = (offer["first_month_price"] if fixed_price_offer else
                  tariffs.discounted_price(tier.price, discount_pct) if tier.price is not None else None)

    snapshot_items = []
    if product_count is not None:
        cat_count = len(offer.get("categories") or [])
        word = _pl_tovar(product_count)
        adj = "активный" if word == "товар" else "активных"
        label = f"{adj} {word}" + (f" в {cat_count} категориях" if cat_count else "")
        snapshot_items.append((f"{product_count:,}".replace(",", " "), label))
    if offer.get("orders_label"):
        snapshot_items.append((offer["orders_label"], "заказов за всё время на Kaspi"))
    if offer.get("years_label"):
        snapshot_items.append((offer["years_label"], "в Kaspi Магазине"))

    now = datetime.now(_ALMATY)
    doc_number = f"КП № {offer['slug'].upper()}-{now:%Y-%m}"
    doc_date = f"Алматы, {now:%d.%m.%Y}"

    return {
        "request": request,
        "offer": offer,
        "tier": tier,
        "prev_tier": prev,
        "all_tiers": tariffs.TIERS,
        "discount_pct": discount_pct,
        "discounted_price": discounted,
        "monthly_price": monthly_price,
        "fixed_price_offer": fixed_price_offer,
        "recommendation_text": _recommendation_text(product_count, tier, prev),
        "snapshot_items": snapshot_items,
        "doc_number": doc_number,
        "doc_date": doc_date,
        "wa_link": _wa_link(offer["client_name"], tier.name),
    }


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"


@app.get("/{slug}")
def render_offer(slug: str, request: Request):
    offer = db.get_offer(slug)
    if not offer:
        raise HTTPException(status_code=404, detail="Предложение не найдено")
    ctx = _build_context(request, offer)
    return templates.TemplateResponse(request, "offer.html", ctx)


# ---------------------------------------------------------------------------
# Админ-ручки — создание/просмотр/удаление КП, закрыты токеном как в lead_service
# ---------------------------------------------------------------------------


class OfferIn(BaseModel):
    slug: str
    client_name: str
    shop_url: str | None = None
    product_count: int | None = None
    orders_label: str | None = None
    years_label: str | None = None
    categories: list[dict] = []
    discount_pct: int = tariffs.DISCOUNT_PCT_DEFAULT
    tier_override: str | None = None
    first_month_price: int | None = Field(default=None, gt=0)
    monthly_price: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_fixed_prices(self):
        if (self.first_month_price is None) != (self.monthly_price is None):
            raise ValueError("first_month_price и monthly_price задаются вместе")
        if (self.first_month_price is not None
                and self.first_month_price > self.monthly_price):
            raise ValueError("first_month_price не может превышать monthly_price")
        return self


@app.post("/api/admin/offers")
def create_offer(payload: OfferIn, k: str | None = Query(default=None)):
    _require_admin(k)
    db.upsert_offer(payload.model_dump())
    return {"ok": True, "url": f"/{payload.slug}"}


@app.get("/api/admin/offers")
def list_offers(k: str | None = Query(default=None)):
    _require_admin(k)
    return db.list_offers()


@app.delete("/api/admin/offers/{slug}")
def delete_offer(slug: str, k: str | None = Query(default=None)):
    _require_admin(k)
    db.delete_offer(slug)
    return {"ok": True}
