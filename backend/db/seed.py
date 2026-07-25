import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from backend.db.database import AsyncSessionLocal
from backend.db.models import User, Subscription
from backend.security.auth import hash_password

logger = logging.getLogger(__name__)


async def seed_default_admin():
    """Создает администратора по умолчанию с тарифом ENTERPRISE, если его еще нет в БД"""
    admin_email = "owner@example.com"
    admin_pass = "admin123admin_"

    async with AsyncSessionLocal() as db:
        try:
            stmt = select(User).where(User.email == admin_email.lower())
            res = await db.execute(stmt)
            user = res.scalar_one_or_none()

            if not user:
                logger.info(f"🔑 Создание учётной записи администратора: {admin_email}...")
                new_user = User(
                    email=admin_email.lower(),
                    password_hash=hash_password(admin_pass),
                    full_name="Venius Admin",
                    phone="+77070000000",
                    is_active=True,
                    is_verified=True
                )
                db.add(new_user)
                await db.flush()

                new_sub = Subscription(
                    user_id=new_user.id,
                    plan_type="ENTERPRISE",
                    max_stores=10,
                    max_skus_in_auto_price=10000,
                    is_api_enabled=True,
                    valid_until=datetime.utcnow() + timedelta(days=3650)  # 10 лет
                )
                db.add(new_sub)
                await db.commit()
                logger.info(f"✅ Учётная запись {admin_email} (ENTERPRISE) успешно создана!")
            else:
                logger.info(f"ℹ️ Учётная запись {admin_email} уже существует в БД.")
        except Exception as e:
            logger.error(f" Ошибка создания сид-администратора: {e}")
