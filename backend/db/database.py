import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

# Production default PostgreSQL URI
DEFAULT_PG_URL = "postgresql+asyncpg://kaspi_user:kaspi_pass_2026@localhost:5432/kaspi_saas"
RAW_DB_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./kaspi_saas.db")

# Format dialect for SQLAlchemy Async
if RAW_DB_URL.startswith("postgres://"):
    ASYNC_DB_URL = RAW_DB_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif RAW_DB_URL.startswith("postgresql://") and not "+asyncpg" in RAW_DB_URL:
    ASYNC_DB_URL = RAW_DB_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
else:
    ASYNC_DB_URL = RAW_DB_URL

engine_kwargs = {"echo": False, "future": True}
if "sqlite" in ASYNC_DB_URL:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # PostgreSQL Connection Pooling
    engine_kwargs["pool_size"] = 10
    engine_kwargs["max_overflow"] = 20
    engine_kwargs["pool_pre_ping"] = True

engine = create_async_engine(ASYNC_DB_URL, **engine_kwargs)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

class Base(DeclarativeBase):
    pass

async def get_db():
    """FastAPI Dependency for database sessions"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

async def init_db():
    """Создание всех таблиц БД при инициализации"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info(f"💾 База данных инициализирована ({ASYNC_DB_URL.split('://')[0]})")
