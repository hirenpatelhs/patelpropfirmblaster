from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from cryptography.fernet import Fernet, InvalidToken
from jose import JWTError, jwt

from app.core.config import settings


password_hasher = PasswordHasher()
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    return password_hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return password_hasher.verify(hashed, password)
    except Exception:
        return False


def create_access_token(subject: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": subject, "role": role, "iat": now, "exp": now + timedelta(minutes=settings.access_token_minutes)}
    return jwt.encode(payload, settings.app_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.app_secret, algorithms=[ALGORITHM], options={"require_exp": True, "require_sub": True})
    except JWTError as exc:
        raise ValueError("Invalid or expired token") from exc


def encrypt_secret(plaintext: str) -> str:
    if not settings.encryption_key:
        raise RuntimeError("ENCRYPTION_KEY is not configured")
    return Fernet(settings.encryption_key.encode()).encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    if not settings.encryption_key:
        raise RuntimeError("ENCRYPTION_KEY is not configured")
    try:
        return Fernet(settings.encryption_key.encode()).decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Credential ciphertext is invalid") from exc
