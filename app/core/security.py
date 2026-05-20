import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Password Hashing (Hardened)
# ─────────────────────────────────────────────────────────────
pwd_context = CryptContext(
    schemes=["bcrypt"],
    bcrypt__rounds=12,
    deprecated="auto",
)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return pwd_context.verify(plain, hashed)
    except Exception as e:
        logger.warning(f"Password verification failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# JWT Helpers
# ─────────────────────────────────────────────────────────────
def _base_payload(user_id: uuid.UUID) -> dict[str, Any]:
    return {
        "sub": str(user_id),
        "iss": "vakilsuite",
        "aud": "vakilsuite-users",
        "jti": str(uuid.uuid4()),
    }


def create_access_token(user_id: uuid.UUID, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )

    payload = {
        **_base_payload(user_id),
        "role": role,
        "exp": expire,
        "type": "access",
    }

    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(user_id: uuid.UUID) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )

    payload = {
        **_base_payload(user_id),
        "exp": expire,
        "type": "refresh",
    }

    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


# ─────────────────────────────────────────────────────────────
# Token Decoding (Hardened)
# ─────────────────────────────────────────────────────────────
def _decode_token(token: str) -> Optional[dict[str, Any]]:
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            audience="vakilsuite-users",
            issuer="vakilsuite",
        )
        return payload
    except JWTError as e:
        logger.warning(f"JWT decode failed: {str(e)}")
        return None


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    payload = _decode_token(token)
    if not payload:
        return None

    if payload.get("type") != "access":
        logger.warning("Invalid token type for access token")
        return None

    return payload


def decode_refresh_token(token: str) -> Optional[dict[str, Any]]:
    payload = _decode_token(token)
    if not payload:
        return None

    if payload.get("type") != "refresh":
        logger.warning("Invalid token type for refresh token")
        return None

    return payload


# ─────────────────────────────────────────────────────────────
# Future Hook (No Breaking Change)
# ─────────────────────────────────────────────────────────────
# NOTE: Token revocation intentionally not enforced yet
# to avoid breaking existing flow.
#
# Future:
#
# def is_token_revoked(jti: str) -> bool:
#     ...
#
# if is_token_revoked(payload["jti"]):
#     return None
