import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Boolean, Integer, Float, Text, DateTime, ForeignKey, Column
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

    sku: Mapped[str] = mapped_column(String(100), primary_key=True)
    store_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=True, index=True)
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
    store_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=True, index=True)
    group_name: Mapped[str] = mapped_column(String(255), nullable=False)
    shared_stock: Mapped[int] = mapped_column(Integer, default=0)
    linked_skus: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON-stringified list
    external_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    dumping_config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-stringified dict
    status: Mapped[str] = mapped_column(String(50), default="synced")
    created_at: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_synced_at: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    last_sync_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sync_logs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON-stringified list


class ProductOverrideDB(Base):
    __tablename__ = "product_overrides"

    sku: Mapped[str] = mapped_column(String(100), primary_key=True)
    store_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("stores.id", ondelete="CASCADE"), nullable=True, index=True)
    price_num: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stock: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

