import os
import uuid
import calendar
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from passlib.context import CryptContext
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "kaspi_saas_super_secret_jwt_key_2026_change_in_production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 30

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

def hash_password(password: str) -> str:
    """Хеширование пароля (PBKDF2-SHA256)"""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверка пароля по хешу"""
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Генерация короткоживущего AccessToken (15 минут)"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire, "iat": datetime.utcnow(), "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_refresh_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Генерация долгоживущего RefreshToken (30 дней)"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS))
    jti = str(uuid.uuid4())
    to_encode.update({"exp": expire, "iat": datetime.utcnow(), "type": "refresh", "jti": jti})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """Декодирование и верификация JWT токена"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        logger.warning(f"Ошибка валидации JWT: {e}")
        return None


_TOKEN_LIFETIME_SEC = {
    "access": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    "refresh": REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600,
}


def is_token_revoked(payload: Dict[str, Any], sessions_revoked_at: Optional[datetime]) -> bool:
    """Выдан ли токен раньше, чем пользователю отозвали сессии (сброс пароля).

    sessions_revoked_at = NULL — ничего не отзывалось, токен живёт как жил.
    Токены до 2026-09-25 выдавались без iat — время выдачи восстанавливаем
    как exp минус стандартный срок жизни (других сроков в коде нет).
    Сравнение по целым секундам: токен, выданный в ту же секунду, что и
    отзыв, считается новым — иначе вход сразу после сброса мог бы погаснуть.
    """
    if sessions_revoked_at is None:
        return False
    iat = payload.get("iat")
    if iat is None:
        exp = payload.get("exp")
        lifetime = _TOKEN_LIFETIME_SEC.get(payload.get("type"))
        if exp is None or lifetime is None:
            return True
        iat = exp - lifetime
    return int(iat) < calendar.timegm(sessions_revoked_at.utctimetuple())
