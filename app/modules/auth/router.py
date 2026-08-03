"""Authentication routes (public login/register/refresh + authenticated /me)."""
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import get_settings
from app.core.ratelimit import rate_limit
from app.core.roles import ROLES, SUPER_ADMIN
from app.core.security import (Principal, get_principal, require_permission,
                                require_platform_admin)
from app.modules.auth import service
from app.modules.auth.dto import (AcceptInvitationRequest, CreateInvitationRequest,
                                  EmailOnlyRequest, ForgotPasswordRequest, LoginRequest,
                                  LogoutRequest, RefreshRequest, RegisterCompanyRequest,
                                  ResetPasswordRequest, UpdateUserRequest, VerifyEmailRequest)

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])

# Company owners/admins manage their team; "*" holders (Admin/Super Admin) qualify.
require_admin = require_permission("admin.users")


@router.post("/login")
def login(payload: LoginRequest, _rl: None = rate_limit("login")):
    return service.login(payload.email, payload.password)


@router.post("/register-company", status_code=status.HTTP_201_CREATED)
def register_company(payload: RegisterCompanyRequest):
    try:
        return service.register_company(
            company_name=payload.companyName, owner_name=payload.ownerName,
            email=payload.email, password=payload.password, currency=payload.currency,
        )
    except HTTPException:
        raise
    except Exception as exc:  # surface the real cause (keeps CORS headers on errors)
        if settings.dev_auth_bypass:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                                f"{type(exc).__name__}: {exc}") from exc
        raise


@router.post("/refresh")
def refresh(payload: RefreshRequest):
    return service.refresh(payload.refreshToken)


# --- Email verification (sign-up OTP) ------------------------------------- #
@router.post("/resend-verification")
def resend_verification(payload: EmailOnlyRequest, _rl: None = rate_limit("verify")):
    return service.request_email_verification(payload.email)


@router.post("/verify-email")
def verify_email(payload: VerifyEmailRequest, _rl: None = rate_limit("verify")):
    return service.verify_email(payload.email, payload.code)


# --- Password reset -------------------------------------------------------- #
@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, _rl: None = rate_limit("reset")):
    return service.request_password_reset(payload.email)


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, _rl: None = rate_limit("reset")):
    return service.reset_password(payload.token, payload.password)


# --- Invitation acceptance (public: invitee has no account yet) ------------ #
@router.get("/invitations/peek")
def peek_invitation(token: str = Query(min_length=8)):
    return service.peek_invitation(token)


@router.post("/invitations/accept", status_code=status.HTTP_201_CREATED)
def accept_invitation(payload: AcceptInvitationRequest, _rl: None = rate_limit("accept")):
    return service.accept_invitation(payload.token, payload.name, payload.password)


@router.post("/logout")
def logout(payload: LogoutRequest | None = None):
    # Access token is stateless; revoke the refresh token so it can't be rotated.
    return service.logout(payload.refreshToken if payload else None)


@router.get("/me")
def me(principal: Principal = Depends(get_principal)):
    prof = service.me(principal.user_id, principal.tenant_id) if principal.user_id else None
    if prof is None:
        # Dev bypass (no real user row) — synthesize from the principal.
        return {
            "userId": principal.user_id, "email": principal.email, "name": principal.email,
            "role": principal.role, "permissions": principal.permissions,
            "isPlatformAdmin": principal.is_platform_admin,
            "company": {"id": principal.tenant_id, "name": None},
        }
    return prof


@router.get("/roles")
def assignable_roles(_: Principal = Depends(require_admin)):
    # Roles a company admin may assign (Super Admin is platform-only).
    return {"roles": [r for r in ROLES if r != SUPER_ADMIN]}


# --- Team management (company admin) --------------------------------------- #
@router.get("/users")
def list_users(principal: Principal = Depends(require_admin)):
    return {"users": service.list_users(principal.tenant_id)}


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UpdateUserRequest,
                principal: Principal = Depends(require_admin)):
    return service.update_user(
        tenant_id=principal.tenant_id, user_public_id=user_id,
        actor_public_id=principal.user_id, role=payload.role, is_active=payload.isActive,
    )


@router.get("/invitations")
def list_invitations(principal: Principal = Depends(require_admin)):
    return {"invitations": service.list_invitations(principal.tenant_id)}


@router.post("/invitations", status_code=status.HTTP_201_CREATED)
def create_invitation(payload: CreateInvitationRequest,
                      principal: Principal = Depends(require_admin)):
    return service.create_invitation(
        tenant_id=principal.tenant_id, inviter_public_id=principal.user_id,
        email=payload.email, role=payload.role,
    )


@router.delete("/invitations/{invite_id}")
def revoke_invitation(invite_id: str, principal: Principal = Depends(require_admin)):
    return service.revoke_invitation(principal.tenant_id, invite_id)


# --- Platform overview (Super Admin, cross-company) ------------------------ #
@router.get("/companies")
def list_companies(_: Principal = Depends(require_platform_admin)):
    return {"companies": service.list_companies()}


@router.post("/dev/bootstrap")
def dev_bootstrap():
    if not settings.dev_auth_bypass:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    try:
        return service.dev_bootstrap()
    except HTTPException:
        raise
    except Exception as exc:  # dev-only: surface the real cause in the response
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"{type(exc).__name__}: {exc}") from exc
