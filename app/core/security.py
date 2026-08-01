"""Microsoft Entra External ID token validation (plan §7).

Validates the incoming Bearer JWT against the CIAM tenant's published JWKS and
extracts the principal (external id, email, tenant, roles). Signing keys are
discovered from the OpenID configuration and cached by PyJWKClient.
"""
from dataclasses import dataclass, field
from functools import lru_cache

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

settings = get_settings()
# auto_error=False so the dev bypass can run without an Authorization header.
_bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    external_id: str
    email: str | None
    tenant_id: str | None            # None until the user has created/joined an org
    roles: list[str] = field(default_factory=list)


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    oidc = httpx.get(settings.entra_openid_config, timeout=10).json()
    return jwt.PyJWKClient(oidc["jwks_uri"])


def get_principal(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    # Local-dev shortcut: skip token validation entirely (plan §7 says never in prod).
    if settings.dev_auth_bypass:
        return Principal(
            external_id=settings.dev_external_id,
            email=settings.dev_email,
            tenant_id=settings.dev_tenant_id or None,
            roles=["Owner"],
        )

    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = creds.credentials
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings.entra_api_audience,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token") from exc

    return Principal(
        external_id=claims.get("oid") or claims["sub"],
        email=claims.get("email") or claims.get("preferred_username"),
        tenant_id=claims.get("extension_tenantId") or claims.get("tid_app"),
        roles=claims.get("roles", []),
    )


def require_tenant(principal: Principal = Depends(get_principal)) -> Principal:
    """Guard for endpoints that require an onboarded tenant."""
    if not principal.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No organization — complete onboarding first")
    return principal
