import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)

RAW_DB_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./kaspi_saas.db")

# Format dialect for SQLAlchemy Async
if RAW_DB_URL.startswith("postgres://"):
    ASYNC_DB_URL = RAW_DB_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif RAW_DB_URL.startswith("postgresql://"):
    ASYNC_DB_URL = RAW_DB_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
elif not ("+asyncpg" in RAW_DB_URL or "+aiosqlite" in RAW_DB_URL):
    ASYNC_DB_URL = "sqlite+aiosqlite:///./kaspi_saas.db"
else:
    ASYNC_DB_URL = RAW_DB_URL

connect_args = {"check_same_thread": False} if "sqlite" in ASYNC_DB_URL else {}

engine = create_async_engine(
    ASYNC_DB_URL,
    echo=False,
    connect_args=connect_args,
    future=True
)

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
    logger.info(f" База данных успешно инициализирована ({ASYNC_DB_URL.split('://')[0]})")
