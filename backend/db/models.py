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
    # Аккаунт заведён владельцем магазина как сотрудник, пароль выдан
    # одноразовый (см. routers/employees.py) — до его смены кабинет
    # показывает только экран смены пароля. Владелец видел этот пароль,
    # поэтому пускать с ним дальше первого входа нельзя.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Тарификация (2026-08-12): платформенная роль суперадмина — полный
    # доступ к /api/admin/* (все магазины, тарифы, балансы, рефералы).
    # Ортогонально referral_profiles (наличие строки = "этот юзер реферер")
    # и UserStore.role (авторизация ВНУТРИ конкретного магазина) — три разные
    # оси, намеренно не сведены в один enum.
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Реф-код с лендинга, сохранённый при регистрации ДО того, как у юзера
    # появился первый магазин (атрибуция происходит позже, в
    # connect-full-store, когда есть store_id, на который вешается
    # store_referrals). Очищается после первой успешной атрибуции.
    pending_referral_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # Онбординг-тур в кабинете (2026-08-22): NULL — ещё не проходил/не завершил,
    # timestamp — когда завершил или явно пропустил. Per-user, не per-store —
    # тур про интерфейс кабинета в целом, а не про конкретный магазин.
    onboarding_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
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


class ClientMailbox(Base):
    """Скелет провижининга персональных адресов на *@buyshark.kz — по одному
    на каждого клиента/сотрудника, которого сервис заводит на себя в Kaspi MC
    (см. AUTOCONNECT_PLAN.md). Реального клиента под это пока нет, поэтому
    ничего сюда не пишет — это задел на будущее: когда Фаза 1 автоподключения
    будет реализована, create_employee_and_fetch_token() должен вызывать
    provision_mailbox() (backend/services/client_mailbox.py) ДО создания
    сотрудника в кабинете Kaspi, чтобы иметь готовый уникальный email и запись
    для последующего сопоставления с IncomingEmail.mailbox_id.

    owner_user_id, а не store_id — потому что на момент провижининга (SMS-вход
    владельца) магазина в БД ещё нет, есть только залогиненный User сервиса
    buyshark. store_id проставляется позже, когда Store появляется (Фаза 2)."""
    __tablename__ = "client_mailboxes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    owner_user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    store_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("stores.id", ondelete="SET NULL"), nullable=True)
    label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IncomingEmail(Base):
    """Письма, принятые Cloudflare Email Routing на *@buyshark.kz и
    переданные вебхуком (backend/routers/webhooks_incoming_email.py) — в
    первую очередь приветственные письма Kaspi MC с временными паролями для
    сотрудников, создаваемых сервисом на себя (см. AUTOCONNECT_PLAN.md,
    generate_employee_credentials() в kaspi_auto_connect.py)."""
    __tablename__ = "incoming_emails"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    to_address: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    from_address: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[Optional[str]] = mapped_column(String(998), nullable=True)
    encrypted_raw: Mapped[str] = mapped_column(Text, nullable=False)
    # Проставляется вебхуком при получении, если to_address совпал с
    # известным ClientMailbox.email — так письмо сразу привязано к клиенту,
    # без отдельного шага сопоставления.
    mailbox_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("client_mailboxes.id", ondelete="SET NULL"), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
    # Сотрудники и роли (2026-09-05, миграция 040). Две ОРТОГОНАЛЬНЫЕ оси прав,
    # подробное обоснование — в backend/security/sections.py:
    #   role             — глубина прав (смотреть/работать/управлять);
    #   allowed_sections — ширина прав: JSON-список ключей разделов кабинета.
    # NULL в allowed_sections = «ограничений нет» (все владельцы и все строки,
    # существовавшие до миграции). Пустой список — это НЕ NULL, это «ничего
    # не разрешено», валидное состояние отстранённого сотрудника.
    allowed_sections: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Код должностного пресета (sections.JOB_PRESETS) — чисто информативный:
    # права всегда читаются из role+allowed_sections, а не из этого поля,
    # иначе правка пресета в коде задним числом меняла бы права живых
    # сотрудников. Хранится, чтобы UI показывал «Сборщик», а не список из
    # пяти галочек, и чтобы было видно, где владелец отошёл от пресета.
    job_preset: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    job_title: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

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
    dump_step: Mapped[float] = mapped_column(Float, default=2.0)
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
    # Долг: сколько штук продано сверх остатка. shared_stock обрезается по
    # нулю (отрицательный остаток нельзя слать в Kaspi), и без этого поля
    # факт перепродажи терялся молча — см. миграцию 031.
    oversold: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Сколько штук общего остатка съедает ОДНА проданная единица товара. Для
    # обычного товара 1. Для группы, где остаток считается в мелких единицах, а
    # продаётся комплектом ("Палочки NG, 7 шт" при остатке в палочках) — 7.
    # Раньше это число угадывалось по хвосту артикула и угадывалось неверно,
    # см. миграцию 034 и AUTO_DEDUCTION_ENGINE.md.
    deduct_per_unit: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # Уже уведомили о текущем снятии по стоп-кол-ву — не переспамить владельца
    # на каждый следующий заказ, пока группа не пополнится выше stop_qty.
    # См. миграцию 039 и notify_zero_stock_telegram.
    stop_notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dumping_config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-stringified dict
    # SKU из linked_skus, исключённые из ГРУППОВОГО демпинга (dumping_config
    # выше их игнорирует) — но их собственный individual dumping_config (см.
    # dumping_configs таблицу) продолжает работать как обычно, если задан.
    dumping_excluded_skus: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-stringified list
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


class KaspiOfferSyncDB(Base):
    """
    Одна строка на (магазин, SKU) — всё состояние синхронизации оффера с Kaspi.

    Отдельной таблицы-очереди намеренно НЕТ: очередь на запись — это просто
    строки, где желаемое (desired_*) расходится с фактическим, отсортированные
    по priority/desired_at. Три слоя в одной строке:

    * desired_*  — что мы хотим видеть на Kaspi (кладут демпер и Мастер-Группы);
    * pushed_*   — что мы реально отправили и когда (дешёвая проверка "не шлём
                   то же самое повторно" без единого запроса);
    * kaspi_*    — что Kaspi реально показывает (из bff/offer-view/list, квоту
                   не тратит). Нужен, потому что в кабинет пишем не только мы:
                   на проде видны записи и от других аккаунтов мерчанта.
    """

    __tablename__ = "kaspi_offer_sync"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    sku: Mapped[str] = mapped_column(String(255), primary_key=True)

    # Реквизиты для сборки payload на /pricefeed/upload/merchant/process
    master_sku: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    store_suffix: Mapped[str] = mapped_column(String(20), default="001")
    city_id: Mapped[str] = mapped_column(String(20), default="750000000")

    # Желаемое состояние (NULL = по этому полю намерений нет)
    desired_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    desired_stock: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    desired_available: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=3, index=True)
    reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    desired_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    # Что реально ушло на Kaspi
    pushed_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pushed_stock: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pushed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Что Kaspi показывает по факту
    kaspi_price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    kaspi_stock: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    kaspi_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    last_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
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
    # Анти-спам флаг "уже уведомили о протухшей чат-сессии" — раньше был
    # in-memory dict (backend/chatbot/config.py), обнулялся при каждом
    # рестарте процесса (в т.ч. деплое), поэтому реально ещё не решённая
    # проблема переуведомлялась на каждый деплой. Персистим, как
    # human_override в ChatStateDB.
    chat_cookie_alert_sent: Mapped[bool] = mapped_column(Boolean, default=False)


class StoreFeatureFlagsDB(Base):
    """Общемагазинные выключатели автоматик — вкладка "Глобальное управление".

    Демпинг и связка остатков раньше можно было выключить только точечно
    (per-SKU / per-group) — не было ни одного общего рубильника на весь
    магазин. Чат-бот сюда не дублируется — у него уже есть свой
    ChatbotSettingsDB.enabled.
    """
    __tablename__ = "store_feature_flags"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    dumping_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stock_groups_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    autoassembly_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
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


class DashboardLayoutDB(Base):
    """Персональная раскладка дашборда (вкладка "Обзор") — видимость, порядок
    и настройки виджетов конкретного пользователя. Персональная, а не по
    магазину: пользователь работает с несколькими магазинами, но хочет
    одинаково настроенный дашборд везде. layout_json хранит только оверрайды
    поверх дефолтного реестра виджетов на фронте (frontend/src/dashboard/
    widgetRegistry.ts) — так добавление нового виджета в будущем не ломает
    уже сохранённые раскладки."""
    __tablename__ = "dashboard_layouts"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    layout_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DashboardRoleDefaultsDB(Base):
    """Дефолтная видимость виджетов дашборда для роли (MANAGER/VIEWER) внутри
    конкретного магазина — настраивает OWNER, чтобы не объяснять каждому
    сотруднику вручную. Оверрайд ниже: registry default -> этот ролевой
    дефолт -> персональный DashboardLayoutDB пользователя (см. выше) —
    личный выбор сотрудника всегда побеждает."""
    __tablename__ = "dashboard_role_defaults"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20), primary_key=True)  # 'MANAGER' | 'VIEWER'
    layout_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
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
    # Имя клиента (для читаемого списка "Чаты" в боте) + отметка времени
    # блокировки для ИИ — используется, чтобы протухшие блокировки (менеджер
    # забыл вернуть ИИ) сами снимались через 2 дня, см. CHAT_OVERRIDE_TTL.
    client_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    blocked_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)


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
    # Кампания «Отзыв» (Фаза 5.1, скан по дням) — False для заказов холодного
    # 90-дневного бэкафилла нового магазина (см. _upsert_sales_orders), чтобы
    # скан не отправил разом просьбу об отзыве по всей истории.
    review_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


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


class KaspiQualityMetricsDailyDB(Base):
    """Один снапшот в день на магазин (upsert по (store_id, date)) из раздела
    кабинета Kaspi "Показатели качества" (#/main-quality-control) — Рейтинг,
    Возвраты, Задержки при передачах, Отмены по вине, Среднее время ответов,
    Доля ответов. Скрапится ночным воркером (kaspi_quality_metrics_worker.py)
    раз в сутки через GraphQL mc.shop.kaspi.kz/mc/facade/graphql — см.
    REAL_IMPORTANT/показатели_качества/KASPI_QUALITY_METRICS_API.md.
    Промотированы только 6 headline-чисел; пороги/гистограмма/доп. поля —
    в raw_payload (JSON), чтобы не плодить миграции при расширении UI.
    """
    __tablename__ = "kaspi_quality_metrics_daily"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[str] = mapped_column(String(10), primary_key=True)  # 'YYYY-MM-DD' (UTC)

    rating_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    returns_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    late_kd_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cancelled_percentage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_chat_response_minutes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    chat_response_rate_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


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
    url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
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
    # Тарификация (2026-08-12): фактически списанные милли-кредиты за этот
    # ответ (с учётом множителя ступени промпта). Позволяет сверять быстрый
    # счётчик store_daily_credits.used_milli с этим аудит-логом построчно.
    # billed_units сохраняет старый смысл (1 сообщение) — не переопределяем.
    credit_units_milli: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
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


# ============================================================================
# Тарификация (2026-08-12) — см. backend/billing/ и HANDOFF.md.
# Тариф и баланс привязаны к МАГАЗИНУ, не к пользователю — это отдельная ось
# от существующей per-user Subscription (та enforce-ит только max_stores,
# т.е. квоту уровня аккаунта "сколько магазинов можно подключить";
# Subscription сознательно не тронута этой миграцией).
# ============================================================================

class StoreTariffDB(Base):
    """Тарифный профиль магазина: план, доплата за кредиты, статус блокировки,
    период продления. Один ряд на магазин. plan_code='DEV' — внутренний
    безлимитный тариф без списаний и блокировок (на него мигрируют все
    существующие магазины при вводе тарификации)."""
    __tablename__ = "store_tariffs"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    plan_code: Mapped[str] = mapped_column(String(30), nullable=False, default="DEV")
    # Договорная цена ГигаПредприятие — перекрывает цену плана из каталога,
    # если задана. NULL = использовать catalog.PLANS[plan_code].monthly_price_minor.
    custom_monthly_price_minor: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # Договорной дневной лимит кредитов ответов ИИ (только ГигаПредприятие) —
    # перекрывает catalog.PLANS[plan_code].daily_credit_limit_milli, если задан.
    # NULL = использовать лимит из каталога плана.
    custom_daily_credits_milli: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    auto_upgrade_credits: Mapped[bool] = mapped_column(Boolean, default=False)
    # 'ACTIVE'|'BLOCKED_LIMIT'|'BLOCKED_BALANCE'
    billing_status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    blocked_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    blocked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    force_unblock_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    billing_anchor_day: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    next_charge_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    # Момент перевода с DEV на платный план — якорь для реферального окна
    # (window_start), НЕ Store.created_at, иначе простой на DEV сжигал бы
    # комиссионные месяцы реферера впустую.
    activated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StoreBalanceDB(Base):
    """Баланс кабинета магазина, тиын (1/100 ₸). Отдельно от StoreTariffDB:
    горячий путь записи (каждое списание) не должен блокироваться на той же
    строке, что редко читаемые тарифные настройки. Может уходить в минус —
    блокировка наступает по catalog.BALANCE_BLOCK_THRESHOLD_MINOR, а не по
    самому факту отрицательного значения."""
    __tablename__ = "store_balances"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    balance_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BillingTransactionDB(Base):
    """Append-only леджер — единственный источник правды по деньгам. Готов
    под платёжный шлюз с первого дня (provider/provider_payment_id/method/
    status), хотя пополнение сейчас только ручное через админку.
    idempotency_key UNIQUE — вся защита от двойного списания/зачисления
    построена на этом констрейнте, а не на read-check-write."""
    __tablename__ = "billing_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)  # знаковое: + начисление, - списание
    balance_after_minor: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # topup_manual|topup_gateway|charge_subscription|
    # charge_prompt_addon|referral_payout|adjustment|refund
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="completed")  # pending|completed|failed|canceled
    provider: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # manual|kaspi_pay|halyk
    provider_payment_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    method: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)  # card|kaspi_qr|bank_transfer|cash
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="KZT")
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, unique=True)
    period_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    period_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by_user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class StoreDailyCreditDB(Base):
    """Дневной счётчик кредитов ответов ИИ-бота. Строка создаётся лениво при
    первом расходе за день — ОТСУТСТВИЕ строки и есть сброс, отдельного
    job'а на полночь не существует (убирает целый класс багов с рестартом
    на границе суток). date — 'YYYY-MM-DD' по часовому поясу Алматы
    (catalog.BILLING_TZ_OFFSET_HOURS), не UTC. limit_milli — снапшот
    дневного лимита на момент создания строки."""
    __tablename__ = "store_daily_credits"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    limit_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    exhausted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class ReferralProfileDB(Base):
    """Наличие строки = "этот пользователь реферер". Ортогонально
    User.is_superadmin и UserStore.role — три разные оси прав."""
    __tablename__ = "referral_profiles"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    commission_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    commission_months: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StoreReferralDB(Base):
    """PK store_id ⇒ у магазина структурно не может быть больше одного
    реферера. window_start/window_end МАТЕРИАЛИЗОВАНЫ (не пересчитываются
    по формуле) — иначе смена тарифа или его данных молча перезапустила бы
    3-месячный отсчёт комиссии. window_start = StoreTariffDB.activated_at
    (первый платный период), а не Store.created_at."""
    __tablename__ = "store_referrals"

    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), primary_key=True)
    referrer_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    referral_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # NULL при ручной привязке
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # 'code'|'manual'
    assigned_by_user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReferralAccrualDB(Base):
    """Append-only начисления рефереру. source_transaction_id UNIQUE —
    идемпотентность: один пополнение → максимум одно начисление. Начисляется
    в момент реального пополнения баланса магазина (topup_*), не в момент
    списания за тариф — решение владельца 2026-08-12."""
    __tablename__ = "referral_accruals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    referrer_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    source_transaction_id: Mapped[str] = mapped_column(String(36), ForeignKey("billing_transactions.id", ondelete="CASCADE"), nullable=False, unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    percent: Mapped[int] = mapped_column(Integer, nullable=False)  # снапшот на момент начисления
    period_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    period_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="accrued")  # accrued|payable|paid|canceled
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    payout_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)



class AssistantConversationDB(Base):
    """Переписка владельца магазина с ИИ-помощником кабинета (каркас,
    2026-09-05). Привязана к магазину, а не только к пользователю: сотрудники
    одного стора видят общий контекст, а данные разных сторов не смешиваются —
    тот же принцип, что и у остальных store-scoped таблиц."""
    __tablename__ = "assistant_conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    # Кто начал переписку. NULL допустим — пользователь может быть удалён,
    # переписка магазина от этого пропадать не должна.
    user_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="Новый диалог")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AssistantMessageDB(Base):
    """Сообщение в переписке с помощником. role — те же значения, что у
    провайдера (user/assistant/tool/system, см. backend/assistant/provider.py).
    tool_calls/tool_result хранятся строкой JSON, чтобы показывать в UI трассу
    «чем помощник пользовался» и уметь воспроизвести ход при отладке."""
    __tablename__ = "assistant_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    store_id: Mapped[str] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # JSON-массив вызовов инструментов этого хода (может быть пустым).
    tool_trace: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
