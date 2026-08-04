import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash


password_hash = PasswordHash.recommended()
JWT_SECRET = os.getenv("TONGUE_SMART_JWT_SECRET", "development-only-secret-change-me-before-deployment")
JWT_ALGORITHM = "HS256"
ACCESS_MINUTES = 15
REFRESH_DAYS = 7


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return password_hash.verify(password, encoded)


def create_access_token(user_id: str, role: str) -> tuple[str, int]:
    now = datetime.now(UTC)
    expires = now + timedelta(minutes=ACCESS_MINUTES)
    token = jwt.encode(
        {"sub": user_id, "role": role, "type": "access", "iat": now, "exp": expires},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    return token, ACCESS_MINUTES * 60


def decode_access_token(token: str) -> dict[str, object]:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("wrong token type")
    return payload


def new_refresh_token() -> tuple[str, str, datetime]:
    token = secrets.token_urlsafe(48)
    return token, digest_token(token), datetime.now(UTC) + timedelta(days=REFRESH_DAYS)


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
