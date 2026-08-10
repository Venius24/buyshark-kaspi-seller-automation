import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Boolean, Integer, BigInteger, Float, Text, DateTime, ForeignKey, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.db.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Пользователь создан через приглашение сотрудника в Telegram-боте (см.
    # TelegramLinkDB/TelegramLinkTokenDB) — не имеет реального пароля, вход в
    # веб-кабинет для него заблокирован на уровне auth_v1.py, существует
    # только для того, чтобы иметь UserStore-запись (роль в конкретном сторе).
    is_telegram_only: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    subscription: Mapped[Optional["Subscription"]] = relationship("Subscription", back_populates="user", uselist=False, cascade="all, delete-orphan")
    user_stores: Mapped[List["UserStore"]] = relationship("UserStore", back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[List["UserSession"]] = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_type: Mapped[str] = mapped_column(String(50), default="STARTER")  # 'STARTER', 'BUSINESS', 'ENTERPRISE'
    max_stores: Mapped[int] = mapped_column(Integer, default=1)
    max_skus_in_auto_price: Mapped[int] = mapped_column(Integer, default=100)
    is_api_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_until: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="subscription")


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    merchant_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_cookies: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    encrypted_api_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    encrypted_mc_email: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    encrypted_mc_password: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    encrypted_chat_cookies: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="ACTIVE")  # 'ACTIVE', 'COOKIE_EXPIRED', 'PAUSED'
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user_stores: Mapped[List["UserStore"]] = relationship("UserStore", back_populates="store", cascade="all, delete-orphan")


class UserStore(Base):
    __tablename__ = "user_stores"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(50), default="OWNER")  # 'OWNER', 'MANAGER', 'VIEWER'

    user: Mapped["User"] = relationship("User", back_populates="user_stores")
    store: Mapped["Store"] = relationship("Store", back_populates="user_stores")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    user_agent: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_active: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="sessions")


class DumpingConfigDB(Base):
    __tablename__ = "dumping_configs"

    # Слайс 2: составной PK (store_id, sku) — раньше PK был только sku, и два
    # магазина с одинаковым SKU физически перезаписывали конфиг друг друга.
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    sku: Mapped[str] = mapped_column(String(100), primary_key=True)
    min_price: Mapped[float] = mapped_column(Float, default=0.0)
    max_price: Mapped[float] = mapped_column(Float, default=0.0)
    dump_step: Mapped[float] = mapped_column(Float, default=10.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    mode: Mapped[str] = mapped_column(String(50), default="TOP_1")
    target_position: Mapped[int] = mapped_column(Integer, default=1)
    max_delivery_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    min_seller_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    same_city_only: Mapped[bool] = mapped_column(Boolean, default=False)
    blacklisted_merchants: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-stringified list
    city_code: Mapped[str] = mapped_column(String(50), default="750000000")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MasterStockGroupDB(Base):
    __tablename__ = "master_stock_groups"

    group_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    # Слайс 2: store_id обязателен (group_id уже глобально уникален благодаря
    # случайному uuid-суффиксу — составной PK тут не нужен, в отличие от sku).
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    group_name: Mapped[str] = mapped_column(String(255), nullable=False)
    shared_stock: Mapped[int] = mapped_column(Integer, default=0)
    linked_skus: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON-stringified list
    external_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Стоп кол-во: при shared_stock <= stop_qty группа снимается с продажи
    # (архивируется на Kaspi) и шлётся телеграм-уведомление — см. миграцию 024.
    stop_qty: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    dumping_config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-stringified dict
    status: Mapped[str] = mapped_column(String(50), default="synced")
    created_at: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_synced_at: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_sync_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sync_logs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-stringified list


class ProductOverrideDB(Base):
    __tablename__ = "product_overrides"

    # Слайс 2: составной PK (store_id, sku) — тот же риск коллизии, что и у
    # DumpingConfigDB, тем же способом закрыт.
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    sku: Mapped[str] = mapped_column(String(100), primary_key=True)
    price_num: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stock: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StoreProductDB(Base):
    __tablename__ = "store_products"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON-stringified list товаров
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ApiKeyDB(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    prefix: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    last_used: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WebhookConfigDB(Base):
    __tablename__ = "webhook_configs"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    url: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    encrypted_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)


class ChatbotSettingsDB(Base):
    __tablename__ = "chatbot_settings"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    admin_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON-stringified list
    updated_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)


class StoreBotProfileDB(Base):
    """Профиль ИИ-менеджера чат-бота этого магазина — имя, адрес самовывоза,
    график, ассортимент и т.д., подставляются в универсальный систем-промпт
    (backend/chatbot/prompt.py). Раньше всё это было хардкодом под BARKHAN
    прямо в chatbot/ai.py. custom_instructions — свободный доп.-блок от
    владельца, prompt_tier — кэш тарифа, посчитанного по его длине."""
    __tablename__ = "store_bot_profiles"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    profile_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    custom_instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    custom_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    prompt_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="base")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatStateDB(Base):
    __tablename__ = "chat_states"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    last_replied_msg_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    human_override: Mapped[bool] = mapped_column(Boolean, default=False)
    # Фаза 5 (кампания «Отзыв»): ставится при отправке campaign='review',
    # снимается на первом же ответе клиента после этого — см. миграцию 022.
    awaiting_review_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)


class ProcessedOrderDB(Base):
    __tablename__ = "processed_orders"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)  # 'radar' | 'packaging'
    order_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON-stringified dict


class PrintHistoryDB(Base):
    __tablename__ = "print_history"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # 'YYYY-MM-DD'
    order_key: Mapped[str] = mapped_column(String(255), primary_key=True)


class DumperLogDB(Base):
    __tablename__ = "dumper_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-stringified event dict


class AnalyticsSnapshotDB(Base):
    __tablename__ = "analytics_snapshots"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON-stringified dict
    updated_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)


class SalesOrderDB(Base):
    """Реальная история завершённых заказов Kaspi (state=ARCHIVE, status=COMPLETED),
    подтягивается фоновым воркером (backend/services/sales_sync_worker.py) —
    Kaspi Order API отдаёт максимум 14 дней за один запрос, поэтому это
    единственный способ иметь настоящую выручку/тренды за период дольше 2 недель.
    """
    __tablename__ = "sales_orders"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    order_internal_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    order_code: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    total_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    state: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    entries_payload: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON [{sku,name,qty,totalPrice}]
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class AnalyticsDailyHistoryDB(Base):
    """Один снапшот в день на магазин (upsert по (store_id, date)) — в отличие
    от AnalyticsSnapshotDB (перезаписывается каждый цикл демпинга, хранит
    только "сейчас"), эта таблица копит историю по дням. Нужна для честного
    графика динамики доли лучшей цены и streak-счётчиков на дашборде —
    начинает копиться с момента деплоя этой фичи, задним числом не восстанавливается.
    """
    __tablename__ = "analytics_daily_history"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # 'YYYY-MM-DD' (UTC)

    best_price_share: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    winning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_saved_margin: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class PriceWarSnapshotDB(Base):
    """Последний известный расклад демпинг-войны по каждому включённому SKU —
    перезаписывается на каждом ~5-минутном цикле демпинга (piggyback на уже
    идущий запрос конкурентов в brain.py, без единого лишнего похода в Kaspi API).
    В отличие от AnalyticsSnapshotDB (агрегат по всему магазину), здесь разрез
    по товару — нужен для сводки "война по всем товарам" на экране демпинга.
    """
    __tablename__ = "price_war_snapshots"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    sku: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    our_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    our_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    competitors_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    best_competitor_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    best_competitor_name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class CompetitorEncounterLogDB(Base):
    """Одна запись за цикл демпинга на SKU, где мы НЕ на 1-м месте — кто именно
    нас обошёл. Копится с момента деплоя фичи; агрегация COUNT(*) GROUP BY
    competitor_name за период = лидерборд конкурентов."""
    __tablename__ = "competitor_encounter_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    competitor_name: Mapped[str] = mapped_column(String(300), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ChatResponseLogDB(Base):
    """Одна запись на каждый реальный авто-ответ чат-бота — нужна для heatmap
    активности чата (по аналогии с dumper_logs), а с Фазы 2 и для тарификации.
    kind различает платный вызов ИИ (inbound_reply) от бесплатного
    логистического шаблона (inbound_logistics_template) и будущих исходящих
    рассылок (outbound_review/outbound_pickup, которые в счётчик не идут —
    billed_units=0). prompt_tier/model/токены фиксируются на момент ответа,
    т.к. тариф магазина может измениться позже. Копится с момента деплоя фичи."""
    __tablename__ = "chat_response_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="inbound_reply")
    prompt_tier: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    billed_units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class OutboundQueueDB(Base):
    """Очередь исходящих сообщений (Фаза 3) — общий каркас под кампании
    «Самовывоз» (Фаза 4) и «Отзыв» (Фаза 5). Одна запись = одно запланированное
    сообщение по заказу. Уникальность (store_id, order_id, campaign) — дедуп на
    уровне БД, повторный enqueue той же кампании для того же заказа не создаст
    дубликат. message_text рендерится один раз при enqueue (кампании
    детерминированные, шаблон не должен "уехать", если профиль магазина
    изменится между постановкой в очередь и отправкой)."""
    __tablename__ = "outbound_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    campaign: Mapped[str] = mapped_column(String(30), nullable=False)
    group_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    window_start_hour: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    window_end_hour: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    skip_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Фаза 6: когда клиент впервые ответил ПОСЛЕ этого исходящего — для
    # честной статистики "доля ответивших", см. миграцию 023.
    replied_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class OrderChatLinkDB(Base):
    """Кэш order_id -> group_id (Фаза 3). Как только Playwright-первый-контакт
    (или обычный HTTP-скан getDiffGroups) один раз находит group_id заказа —
    он кэшируется тут, и все последующие сообщения по этому заказу (включая
    другие кампании и обычные ИИ-ответы) идут быстрым HTTP путём без повторного
    открытия браузера."""
    __tablename__ = "order_chat_links"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(100), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AutoAssemblyRuleDB(Base):
    """Правило «Автосборки» — одно на (store_id, вид доставки). Три стадии
    конвейера включаются независимо: accept (принять заказ) -> assemble
    (упаковать/собрать груз) -> waybill (скачать накладную в спул печати).
    delivery_type — те же категории, что возвращает
    KaspiAPI._parse_delivery_type (Express/Postomat/Самовывоз/Today/Tomorrow/
    Kaspi Доставка).
    """
    __tablename__ = "auto_assembly_rules"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    delivery_type: Mapped[str] = mapped_column(String(50), primary_key=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)

    stage_accept: Mapped[bool] = mapped_column(Boolean, default=False)
    stage_assemble: Mapped[bool] = mapped_column(Boolean, default=False)
    stage_waybill: Mapped[bool] = mapped_column(Boolean, default=False)

    # JSON: {"days": [0..6], "start": "09:00", "end": "19:00"} — пустой список
    # days или null-объект целиком = круглосуточно, без ограничения.
    schedule: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    max_order_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sku_blacklist: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON-список SKU
    respect_stock_groups: Mapped[bool] = mapped_column(Boolean, default=True)
    max_per_cycle: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AutoAssemblyLogDB(Base):
    """Журнал решений движка автосборки — по образцу DumperLogDB."""
    __tablename__ = "auto_assembly_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    order_code: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str] = mapped_column(String(20), nullable=False)  # 'accept' | 'assemble' | 'waybill'
    decision: Mapped[str] = mapped_column(String(20), nullable=False)  # 'DONE' | 'SKIPPED' | 'FAILED'
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class WaybillSpoolDB(Base):
    """Накопитель скачанных накладных для живой пачки на печать. unique_key
    намеренно совпадает с get_page_unique_key('order_<code>') из
    print_batcher_service.py — печать пачки идёт через тот же PDF-пайплайн
    и дедуп по PrintHistoryDB, без отдельной логики.
    """
    __tablename__ = "waybill_spool"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    order_code: Mapped[str] = mapped_column(String(100), primary_key=True)
    date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # 'YYYY-MM-DD', день скачивания
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    printed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class DraftProductDB(Base):
    """Черновик фичи «Автодобавление товара» (backend/autolist/). Один ряд —
    один товар от загрузки фото+названия до отправки на модерацию Kaspi.

    draft_payload — единый JSON-блоб текущего редактируемого состояния
    ({category_code, category_name, brand_name, sku, price, stock,
    features: {attributeCode: value}, feature_meta, description}) — старт от
    вывода extractor.py, дальше правится человеком в review UI поверх этого
    же блоба (см. HANDOFF 3.17: показано живьём, что gpt-4o-mini регулярно
    ошибается даже на "заполненных" полях — ревью человеком обязательно для
    ЛЮБОГО поля, не только missing_mandatory, поэтому нет отдельных
    ai_value/edited_value колонок — один источник правды, который редактируют).

    ai_raw — неизменяемый снимок исходного вывода extractor.py (guess_category
    + fill_attributes) для аудита/дебага "что предложил AI vs что отправили".
    """
    __tablename__ = "draft_products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    photo_path: Mapped[str] = mapped_column(String(500), nullable=False)
    raw_title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="draft", index=True)
    # draft -> needs_review -> approved -> submitting -> submitted -> rejected -> error

    ai_raw: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    draft_payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    missing_mandatory: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON-список attributeCode

    kaspi_result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON последнего ответа Kaspi
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TelegramLinkDB(Base):
    """Привязка одного Telegram-аккаунта к одному пользователю кабинета.
    tg_user_id — BigInteger (не Integer): реальные Telegram user ID давно
    превышают 2^31, обычный Integer их бы переполнил/усёк.

    active_store_id — какой из магазинов пользователя сейчас выбран в боте
    (см. /switch в handlers.py); может быть NULL сразу после привязки, если
    у пользователя больше одного магазина и он ещё не выбирал.
    """
    __tablename__ = "telegram_links"

    tg_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    active_store_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("stores.id", ondelete="SET NULL"), nullable=True)
    tg_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tg_first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    language_code: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)  # 'ru'/'kk'/др. из Telegram, Ф5 частичная локализация
    linked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TelegramLinkTokenDB(Base):
    """Одноразовый (или max_uses-разовый) токен для deep-link привязки
    Telegram к магазину (t.me/<bot>?start=<token>).

    user_id=None + invite_role заданы => приглашение НОВОГО сотрудника: бот
    сам заведёт telegram-only User при первом использовании токена.
    user_id задан + invite_role=None => владелец привязывает СЕБЯ (уже
    существующий User) к своему Telegram.
    """
    __tablename__ = "telegram_link_tokens"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    invite_role: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # 'MANAGER' | 'VIEWER'
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StorePrintSettingsDB(Base):
    """Настройки Print-Batcher (раскладка по умолчанию, сортировка,
    экспресс-режим и т.д.) per-store. Раньше это был один общий JSON-файл
    (states/print_settings.json) на все магазины — баг, найденный при
    планировании Telegram-бота (docs/TELEGRAM_BOT_PLAN.md §7.2): второй
    клиент молча делил бы настройки печати с первым."""
    __tablename__ = "store_print_settings"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON-stringified dict
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TelegramNotificationPrefDB(Base):
    """Персональные настройки уведомлений (docs/TELEGRAM_BOT_PLAN.md §4.1, §6)
    — на каждую пару (человек, магазин), не на человека целиком: один и тот
    же Telegram-аккаунт может быть OWNER в одном магазине и MANAGER в другом
    с разными предпочтениями. quiet_from/quiet_to — 'HH:MM' или NULL (тихие
    часы выключены)."""
    __tablename__ = "telegram_notification_prefs"

    tg_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("telegram_links.tg_user_id", ondelete="CASCADE"), primary_key=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    chats: Mapped[bool] = mapped_column(Boolean, default=True)
    problems: Mapped[bool] = mapped_column(Boolean, default=True)
    money: Mapped[bool] = mapped_column(Boolean, default=False)  # самый спамный канал — выкл по умолчанию
    new_order_min_sum: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quiet_from: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    quiet_to: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)


class TelegramAuditLogDB(Base):
    """Аудит действий, совершённых из бота и меняющих состояние магазина
    (docs/TELEGRAM_BOT_PLAN.md §4.4) — вкл/выкл автосборки, смена настроек
    уведомлений, отвязка сотрудника, ответ клиенту из Telegram и т.д."""
    __tablename__ = "telegram_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    tg_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

