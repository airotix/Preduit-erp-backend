"""App-issued JWT helpers (self-managed auth)."""
import datetime

import jwt
from fastapi import HTTPException, status

from app.core.config import get_settings

settings = get_settings()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def create_access_token(*, sub: str, claims: dict) -> str:
    payload = {
        **claims, "sub": str(sub), "type": "access", "iat": _now(),
        "exp": _now() + datetime.timedelta(minutes=settings.jwt_access_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(*, sub: str, company_id: str | None = None, jti: str) -> str:
    payload = {
        "sub": str(sub), "type": "refresh", "companyId": company_id, "jti": jti, "iat": _now(),
        "exp": _now() + datetime.timedelta(days=settings.jwt_refresh_days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_reset_token(*, sub: str, company_id: str, minutes: int = 30) -> str:
    payload = {
        "sub": str(sub), "type": "reset", "companyId": company_id, "iat": _now(),
        "exp": _now() + datetime.timedelta(minutes=minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_invite_token(*, company_id: str, email: str, role: str, days: int = 7) -> str:
    payload = {
        "type": "invite", "companyId": company_id, "email": email, "role": role, "iat": _now(),
        "exp": _now() + datetime.timedelta(days=days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, *, expected_type: str | None = None) -> dict:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token expired") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc
    if expected_type and claims.get("type") != expected_type:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type")
    return claims
