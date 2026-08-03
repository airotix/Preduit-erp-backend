"""Authentication & authorization.

Self-managed auth: the app issues its own JWT (see modules/auth/tokens.py). This
module turns a Bearer token into a Principal and provides the tenant + permission
guards. A local-dev bypass short-circuits token validation for convenience.
"""
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.roles import has_permission, permissions_for
from app.modules.auth.tokens import decode_token

settings = get_settings()
# auto_error=False so the dev bypass can run without an Authorization header.
_bearer = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    user_id: str | None
    email: str | None
    tenant_id: str | None            # company id; None until the user has an org
    role: str | None = None
    permissions: list[str] = field(default_factory=list)
    is_platform_admin: bool = False
    external_id: str | None = None


def get_principal(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    # Local-dev shortcut: skip token validation entirely (never in prod).
    if settings.dev_auth_bypass:
        return Principal(
            user_id=None, email=settings.dev_email,
            tenant_id=settings.dev_tenant_id or None, role="Admin",
            permissions=["*"], is_platform_admin=False,
            external_id=settings.dev_external_id,
        )

    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    claims = decode_token(creds.credentials, expected_type="access")
    role = claims.get("role")
    perms = claims.get("permissions")
    is_platform = bool(claims.get("isPlatformAdmin"))
    if not perms:  # tolerate older tokens: derive from role
        perms = permissions_for(role, is_platform)
    return Principal(
        user_id=claims.get("sub"),
        email=claims.get("email"),
        tenant_id=claims.get("companyId"),
        role=role,
        permissions=list(perms),
        is_platform_admin=is_platform,
        external_id=claims.get("externalId"),
    )


def require_tenant(principal: Principal = Depends(get_principal)) -> Principal:
    """Guard for endpoints that require an onboarded tenant/company."""
    if not principal.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No organization — complete onboarding first")
    return principal


def require_platform_admin(principal: Principal = Depends(get_principal)) -> Principal:
    """Guard for cross-company (Super Admin) endpoints."""
    if not principal.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Platform administrators only")
    return principal


def require_permission(permission: str):
    """Dependency factory: allow only principals holding `permission` (or `*`)."""
    def _guard(principal: Principal = Depends(require_tenant)) -> Principal:
        if not has_permission(principal.permissions, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing permission: {permission}")
        return principal
    return _guard
