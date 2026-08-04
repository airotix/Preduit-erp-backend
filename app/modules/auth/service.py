"""Self-managed authentication logic.

Login / registration / refresh run on the privileged system session (RLS-exempt)
because there's no tenant context until a token is issued.
"""
import datetime
import hashlib
import json
import re
import secrets
import uuid

import bcrypt
from fastapi import HTTPException, status
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.database import system_session
from app.core import mailer
from app.core.roles import ADMIN, ROLES, SUPER_ADMIN, permissions_for
from app.models.core import (EmailVerification, Invitation, PasswordReset,
                             RefreshToken, Subscription, Tenant, User)
from app.modules.auth import tokens

settings = get_settings()

# Flow lifetimes.
CODE_TTL_MIN = 15
RESET_TTL_MIN = 30
INVITE_TTL_DAYS = 7
MAX_VERIFY_ATTEMPTS = 5


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _tid(tenant_id) -> uuid.UUID:
    # Coerce to UUID for ORM column comparisons/inserts. These tenant filters are
    # required for correctness in prod, where the erp_system session is RLS-exempt.
    return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))


def _dev_reveal() -> bool:
    # Email delivery isn't wired yet; in dev we return the code/link in the API
    # response so the flows are testable. Never true in production.
    return bool(settings.dev_auth_bypass)


def hash_password(pw: str) -> str:
    # bcrypt operates on <=72 bytes; encode and hash directly (no passlib).
    return bcrypt.hashpw(pw.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(pw.encode("utf-8")[:72], hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# RLS plumbing: the "system" session is only truly exempt when it connects as the
# erp_system principal (prod). Under local trusted-connection dev it isn't, so we
# set/clear the tenant context on the connection to satisfy the policy predicate.
def _set_tenant(db, tenant_id) -> None:
    db.execute(text("EXEC sp_set_session_context @key=N'tenant_id', @value=:tid"),
               {"tid": str(tenant_id)})


def _clear_tenant(db) -> None:
    db.execute(text("EXEC sp_set_session_context @key=N'tenant_id', @value=NULL"))


# --------------------------------------------------------------------------- #
# Profiles & claims
# --------------------------------------------------------------------------- #
def _profile(db, user: User) -> dict:
    tenant = db.get(Tenant, user.tenant_id) if user.tenant_id else None
    perms = permissions_for(user.role, user.is_platform_admin)
    return {
        "userId": str(user.public_id),
        "email": user.email,
        "name": user.display_name or user.email,
        "role": user.role,
        "permissions": perms,
        "isPlatformAdmin": bool(user.is_platform_admin),
        "isOwner": bool(user.is_owner),
        "company": {"id": str(user.tenant_id) if user.tenant_id else None,
                    "name": tenant.name if tenant else None,
                    "currency": tenant.base_currency_code if tenant else None,
                    "setupComplete": bool(tenant.setup_complete) if tenant else True},
    }


def _mint_refresh(db, user: User, prof: dict) -> str:
    """Issue a refresh token and record its jti for rotation / reuse detection."""
    jti = uuid.uuid4()
    token = tokens.create_refresh_token(
        sub=str(user.public_id), company_id=prof["company"]["id"], jti=str(jti))
    if user.tenant_id:  # tenant context is set by the caller, so the insert passes RLS
        db.add(RefreshToken(
            jti=jti, tenant_id=user.tenant_id, user_id=user.id,
            expires_at=_now() + datetime.timedelta(days=settings.jwt_refresh_days), created_at=_now(),
        ))
        db.flush()
    return token


def _issue(db, user: User) -> dict:
    prof = _profile(db, user)
    claims = {
        "email": user.email, "name": prof["name"], "role": user.role,
        "companyId": prof["company"]["id"], "permissions": prof["permissions"],
        "isPlatformAdmin": prof["isPlatformAdmin"], "externalId": user.external_id,
    }
    return {
        "accessToken": tokens.create_access_token(sub=str(user.public_id), claims=claims),
        "refreshToken": _mint_refresh(db, user, prof),
        "user": prof,
    }


def _raise_locked(locked_until: datetime.datetime):
    ms = int(locked_until.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    raise HTTPException(
        status.HTTP_423_LOCKED,
        detail={"message": "Account temporarily locked after repeated sign-in attempts.",
                "lockedUntilMs": ms},
    )


def _register_failed_login(db, user: User) -> None:
    """Increment the failed counter, locking the account past the threshold.
    Committed explicitly so the count survives the 401 that follows (the session
    otherwise rolls back on the raised exception)."""
    user.failed_logins = (user.failed_logins or 0) + 1
    if user.failed_logins >= settings.auth_max_failed_attempts:
        user.locked_until = _now() + datetime.timedelta(minutes=settings.auth_lockout_minutes)
        user.failed_logins = 0
    db.commit()


# --------------------------------------------------------------------------- #
# Endpoints' logic
# --------------------------------------------------------------------------- #
def _find_by_email(db, email: str):
    return db.execute(
        select(User).where(func.lower(User.email) == email.strip().lower())
    ).scalars().first()


def _find_user_global(db, email: str):
    """Locate a user by email without knowing their tenant.

    In prod the system principal is RLS-exempt, so a single scan sees everyone.
    In local dev (trusted connection) it is NOT exempt, so we walk each tenant
    (the tenants table is outside the RLS policy) setting the session context
    until the address turns up. Callers should re-scope to user.tenant_id after.
    """
    _clear_tenant(db)
    user = _find_by_email(db, email)          # prod / exempt: one pass finds it
    if user is not None:
        return user
    for tid in db.execute(select(Tenant.id)).scalars().all():
        _set_tenant(db, tid)
        user = _find_by_email(db, email)
        if user is not None:
            return user
    _clear_tenant(db)
    return None


def login(email: str, password: str) -> dict:
    with system_session() as db:
        user = _find_user_global(db, email)   # finds the user in whatever tenant
        if user is not None:
            _set_tenant(db, user.tenant_id)   # scope reads/writes for this user's tenant
            if user.locked_until and user.locked_until > _now():
                _raise_locked(user.locked_until)   # 423 with unlock time
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            if user is not None and user.is_active:
                _register_failed_login(db, user)   # count toward lockout (commits)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
        user.failed_logins = 0                # success clears the counter and any lock
        user.locked_until = None
        user.last_login = _now()
        return _issue(db, user)


def me(user_id: str, tenant_id: str | None = None) -> dict | None:
    with system_session() as db:
        if tenant_id:
            _set_tenant(db, tenant_id)
        user = db.execute(select(User).where(User.public_id == user_id)).scalars().first()
        return _profile(db, user) if user else None


def _revoke_all_refresh(db, user_id: int) -> None:
    for r in db.execute(
        select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
    ).scalars().all():
        r.revoked_at = _now()


def refresh(refresh_token: str) -> dict:
    """Rotate the refresh token: validate the stored jti, revoke it, and mint a
    fresh pair. Re-presenting an already-rotated token trips reuse detection and
    revokes the whole family (forces a re-login)."""
    claims = tokens.decode_token(refresh_token, expected_type="refresh")
    jti = claims.get("jti")
    with system_session() as db:
        if claims.get("companyId"):
            _set_tenant(db, claims["companyId"])
        user = db.execute(select(User).where(User.public_id == claims.get("sub"))).scalars().first()
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account is inactive")

        row = None
        if jti:
            try:
                row = db.execute(
                    select(RefreshToken).where(RefreshToken.jti == uuid.UUID(str(jti)))
                ).scalars().first()
            except ValueError:
                row = None
        if row is None:  # unknown token (or issued before rotation existed) → re-login
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
        if row.revoked_at is not None:  # rotated token replayed → revoke everything
            _revoke_all_refresh(db, user.id)
            db.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                                "Refresh token reuse detected — please sign in again.")
        if row.expires_at < _now():
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token expired")

        row.revoked_at = _now()          # rotate: retire the presented token
        issued = _issue(db, user)        # mints + records a new refresh token
        return issued


def logout(refresh_token: str | None) -> dict:
    """Best-effort refresh-token revocation on sign-out (stateless access token
    simply expires). Always returns ok — a bad/absent token is a no-op."""
    if not refresh_token:
        return {"status": "ok"}
    try:
        claims = tokens.decode_token(refresh_token, expected_type="refresh")
    except HTTPException:
        return {"status": "ok"}
    jti = claims.get("jti")
    with system_session() as db:
        if claims.get("companyId"):
            _set_tenant(db, claims["companyId"])
        if jti:
            try:
                row = db.execute(
                    select(RefreshToken).where(RefreshToken.jti == uuid.UUID(str(jti)))
                ).scalars().first()
            except ValueError:
                row = None
            if row and row.revoked_at is None:
                row.revoked_at = _now()
    return {"status": "ok"}


def _slugify(name: str, db) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "company"
    slug = base
    while db.execute(select(Tenant).where(Tenant.slug == slug)).scalars().first():
        slug = f"{base}-{uuid.uuid4().hex[:5]}"
    return slug


def register_company(*, company_name: str, owner_name: str, email: str,
                     password: str, currency: str = "EUR") -> dict:
    with system_session() as db:
        _clear_tenant(db)
        if _find_by_email(db, email):
            raise HTTPException(status.HTTP_409_CONFLICT, "That email is already registered")
        slug = _slugify(company_name, db)
        tid = uuid.uuid4()
        _set_tenant(db, tid)   # so tenant/subscription/user inserts pass the block predicate
        db.add(Tenant(id=tid, name=company_name.strip(), slug=slug,
                      base_currency_code=(currency or "EUR").upper()[:3],
                      region="primary", status="Active"))
        db.flush()
        db.add(Subscription(tenant_id=tid, plan="trial", status="trialing", seat_limit=5))
        owner = User(
            tenant_id=tid, external_id=f"local:{uuid.uuid4().hex}",
            email=email.strip(), display_name=owner_name.strip() or email,
            is_owner=True, status="Active", role=ADMIN, is_active=True,
            password_hash=hash_password(password),
        )
        db.add(owner)
        db.flush()
        code = _create_email_code(db, owner)   # seed the sign-up verification OTP
        mailer.send_verification_code(email.strip(), code)
        issued = _issue(db, owner)
        if _dev_reveal():
            issued["devVerifyCode"] = code
        return issued


def complete_company_setup(*, tenant_id: str, actor_public_id: str | None,
                           company_name: str, country: str | None, city: str | None,
                           currency: str, tax_registration: str | None,
                           modules: list[str]) -> dict:
    """Persist the post-signup wizard's Outlets + Modules steps and mark the
    company set up. Team invites (step 3) are created separately via
    create_invitation so each gets its own token/email. Returns the refreshed
    profile so the client can update the signed-in user."""
    with system_session() as db:
        _set_tenant(db, tenant_id)
        tenant = db.get(Tenant, _tid(tenant_id))
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found.")
        tenant.name = company_name.strip()
        tenant.base_currency_code = (currency or tenant.base_currency_code).upper()[:3]
        tenant.country = (country or "").strip() or None
        tenant.city = (city or "").strip() or None
        tenant.tax_registration = (tax_registration or "").strip() or None
        tenant.setup_complete = True
        # Persist enabled modules as a JSON list in system_settings (idempotent).
        db.execute(text("DELETE FROM dbo.system_settings WHERE tenant_id=:t AND [key]='enabled_modules'"),
                   {"t": str(tenant_id)})
        db.execute(text("INSERT INTO dbo.system_settings (tenant_id,[key],value) VALUES (:t,'enabled_modules',:v)"),
                   {"t": str(tenant_id), "v": json.dumps(modules or [])})
        user = None
        if actor_public_id:
            user = db.execute(select(User).where(User.public_id == actor_public_id)).scalars().first()
        return _profile(db, user) if user else {"company": {"id": str(tenant_id), "name": tenant.name,
                                                            "currency": tenant.base_currency_code,
                                                            "setupComplete": True}}


def _profile_from_tenant(t: Tenant) -> dict:
    """Project the company-profile columns into the frontend's JSON shape."""
    return {
        "companyName": t.name or "",
        "about": t.about or "",
        "logoDocId": t.logo_doc_id,
        "coverDocId": t.cover_doc_id,
        "industry": t.industry or "",
        "businessType": t.business_type or "",
        "salesModel": t.sales_model or "",
        "founded": t.founded or "",
        "street": t.street or "",
        "country": t.country or "",
        "city": t.city or "",
        "state": t.state or "",
        "postal": t.postal or "",
        "businessEmail": t.business_email or "",
        "phone": t.phone or "",
        "supportLine": t.support_line or "",
        "openingHours": t.opening_hours or "",
        "website": t.website or "",
        "linkedin": t.social_linkedin or "",
        "instagram": t.social_instagram or "",
        "facebook": t.social_facebook or "",
        "x": t.social_x or "",
        "legalName": t.legal_name or "",
        "sameAsCompany": bool(t.legal_same_as_company),
        "registrationNumber": t.registration_number or "",
        "taxNumber": t.tax_registration or "",
    }


def _clean(v) -> str | None:
    """Trimmed string, or None when empty — keeps columns clean/queryable."""
    s = (v or "").strip() if isinstance(v, str) else v
    return s or None


def get_company_profile(tenant_id: str) -> dict:
    with system_session() as db:
        _set_tenant(db, tenant_id)
        tenant = db.get(Tenant, _tid(tenant_id))
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found.")
        return _profile_from_tenant(tenant)


def save_company_profile(tenant_id: str, p: dict) -> dict:
    with system_session() as db:
        _set_tenant(db, tenant_id)
        t = db.get(Tenant, _tid(tenant_id))
        if t is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found.")
        if (p.get("companyName") or "").strip():
            t.name = p["companyName"].strip()
        t.about = _clean(p.get("about"))
        t.logo_doc_id = _clean(p.get("logoDocId"))
        t.cover_doc_id = _clean(p.get("coverDocId"))
        t.industry = _clean(p.get("industry"))
        t.business_type = _clean(p.get("businessType"))
        t.sales_model = _clean(p.get("salesModel"))
        t.founded = _clean(p.get("founded"))
        t.street = _clean(p.get("street"))
        t.country = _clean(p.get("country"))
        t.city = _clean(p.get("city"))
        t.state = _clean(p.get("state"))
        t.postal = _clean(p.get("postal"))
        t.business_email = _clean(p.get("businessEmail"))
        t.phone = _clean(p.get("phone"))
        t.support_line = _clean(p.get("supportLine"))
        t.opening_hours = _clean(p.get("openingHours"))
        t.website = _clean(p.get("website"))
        t.social_linkedin = _clean(p.get("linkedin"))
        t.social_instagram = _clean(p.get("instagram"))
        t.social_facebook = _clean(p.get("facebook"))
        t.social_x = _clean(p.get("x"))
        t.legal_name = _clean(p.get("legalName"))
        t.legal_same_as_company = bool(p.get("sameAsCompany"))
        t.registration_number = _clean(p.get("registrationNumber"))
        t.tax_registration = _clean(p.get("taxNumber"))
        db.flush()
        return _profile_from_tenant(t)


def dev_bootstrap() -> dict:
    """DEV ONLY: set a password on the demo tenant's owner and ensure a Super
    Admin account, so you can log in immediately. Guarded by dev_auth_bypass."""
    tid_uuid = None
    if settings.dev_tenant_id:
        try:
            tid_uuid = uuid.UUID(settings.dev_tenant_id)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "DEV_TENANT_ID in .env is not a valid GUID")
    made = []
    with system_session() as db:
        if tid_uuid:
            _set_tenant(db, tid_uuid)   # dev: scope to the demo tenant for read+write
            owner = db.execute(
                select(User).where(User.tenant_id == tid_uuid, User.is_owner == True)  # noqa: E712
            ).scalars().first()
            if owner:
                owner.password_hash = hash_password("Admin@12345")
                owner.role = ADMIN
                owner.is_active = True
                made.append({"email": owner.email, "password": "Admin@12345", "role": ADMIN})
        sa = db.execute(
            select(User).where(func.lower(User.email) == "superadmin@preduit.local")
        ).scalars().first()
        if sa is None and tid_uuid:
            sa = User(tenant_id=tid_uuid, external_id=f"local:{uuid.uuid4().hex}",
                      email="superadmin@preduit.local", display_name="Super Admin",
                      is_owner=False, status="Active", role=SUPER_ADMIN,
                      is_platform_admin=True, is_active=True,
                      password_hash=hash_password("Super@12345"))
            db.add(sa)
            made.append({"email": "superadmin@preduit.local", "password": "Super@12345",
                         "role": SUPER_ADMIN})
        return {"created": made}


# --------------------------------------------------------------------------- #
# Email verification (6-digit OTP)
# --------------------------------------------------------------------------- #
def _create_email_code(db, user: User) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(EmailVerification(
        tenant_id=user.tenant_id, user_id=user.id, code_hash=_sha256(code),
        expires_at=_now() + datetime.timedelta(minutes=CODE_TTL_MIN), created_at=_now(),
    ))
    db.flush()
    return code


def _find_user_any_tenant(db, email: str):
    """Locate a user pre-auth across all tenants (see _find_user_global)."""
    return _find_user_global(db, email)


def request_email_verification(email: str) -> dict:
    with system_session() as db:
        user = _find_user_any_tenant(db, email)
        out: dict = {"sent": True}
        if user:
            _set_tenant(db, user.tenant_id)
            code = _create_email_code(db, user)
            mailer.send_verification_code(user.email, code)
            if _dev_reveal():
                out["devCode"] = code
        return out  # neutral response whether or not the address exists


def verify_email(email: str, code: str) -> dict:
    with system_session() as db:
        user = _find_user_any_tenant(db, email)
        if user is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "We couldn't find an account for that email.")
        _set_tenant(db, user.tenant_id)
        # Already verified (e.g. duplicate submit) — just re-issue tokens.
        if user.email_verified:
            return _issue(db, user)
        rows = db.execute(
            select(EmailVerification)
            .where(EmailVerification.user_id == user.id, EmailVerification.consumed_at.is_(None))
            .order_by(EmailVerification.id.desc())
        ).scalars().all()
        active = [r for r in rows if r.expires_at >= _now()]
        if not active:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That code has expired — request a new one.")
        target = _sha256(code.strip())
        match = next((r for r in active if r.code_hash == target), None)
        if match is None:
            newest = active[0]                       # throttle guessing on the newest code
            newest.attempts = (newest.attempts or 0) + 1
            over = newest.attempts > MAX_VERIFY_ATTEMPTS
            db.commit()                              # persist the attempt across the raised error
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS if over else status.HTTP_400_BAD_REQUEST,
                "Too many attempts — request a new code." if over else "That code doesn't match.")
        match.consumed_at = _now()
        user.email_verified = True
        return _issue(db, user)   # verified → hand back fresh tokens so they land signed in


# --------------------------------------------------------------------------- #
# Password reset (single-use, signed token carries the tenant)
# --------------------------------------------------------------------------- #
def request_password_reset(email: str) -> dict:
    with system_session() as db:
        user = _find_user_any_tenant(db, email)
        out: dict = {"sent": True}
        if user and user.is_active:
            _set_tenant(db, user.tenant_id)
            token = tokens.create_reset_token(sub=str(user.public_id),
                                              company_id=str(user.tenant_id), minutes=RESET_TTL_MIN)
            db.add(PasswordReset(
                tenant_id=user.tenant_id, user_id=user.id, token_hash=_sha256(token),
                expires_at=_now() + datetime.timedelta(minutes=RESET_TTL_MIN), created_at=_now(),
            ))
            db.flush()
            link = f"{settings.app_base_url.rstrip('/')}/reset-password?token={token}"
            mailer.send_password_reset(user.email, link)
            if _dev_reveal():
                out["devToken"] = token
        return out  # neutral response — never reveal whether the email exists


def reset_password(token: str, new_password: str) -> dict:
    claims = tokens.decode_token(token, expected_type="reset")
    with system_session() as db:
        if claims.get("companyId"):
            _set_tenant(db, claims["companyId"])
        rec = db.execute(
            select(PasswordReset)
            .where(PasswordReset.token_hash == _sha256(token), PasswordReset.consumed_at.is_(None))
        ).scalars().first()
        if rec is None or rec.expires_at < _now():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired.")
        user = db.execute(select(User).where(User.public_id == claims.get("sub"))).scalars().first()
        if user is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This reset link is invalid or has expired.")
        user.password_hash = hash_password(new_password)
        rec.consumed_at = _now()
        return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Team invitations
# --------------------------------------------------------------------------- #
def _invite_dto(inv: Invitation) -> dict:
    return {
        "id": str(inv.public_id), "email": inv.email, "role": inv.role, "status": inv.status,
        "expiresAt": inv.expires_at.isoformat() if inv.expires_at else None,
        "createdAt": inv.created_at.isoformat() if inv.created_at else None,
    }


def _assignable_role(role: str) -> None:
    if role not in ROLES or role == SUPER_ADMIN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a valid team role.")


def create_invitation(*, tenant_id: str, inviter_public_id: str | None, email: str, role: str) -> dict:
    _assignable_role(role)
    email = email.strip()
    tid = _tid(tenant_id)
    with system_session() as db:
        _set_tenant(db, tenant_id)
        member = db.execute(
            select(User).where(func.lower(User.email) == email.lower(), User.tenant_id == tid)
        ).scalars().first()
        if member:
            raise HTTPException(status.HTTP_409_CONFLICT, "That person is already on your team.")
        # Supersede any earlier pending invite for the same address.
        for prior in db.execute(
            select(Invitation).where(func.lower(Invitation.email) == email.lower(),
                                     Invitation.tenant_id == tid, Invitation.status == "pending")
        ).scalars().all():
            prior.status = "revoked"
        inviter = None
        if inviter_public_id:
            inviter = db.execute(select(User).where(User.public_id == inviter_public_id)).scalars().first()
        token = tokens.create_invite_token(company_id=str(tenant_id), email=email, role=role,
                                           days=INVITE_TTL_DAYS)
        inv = Invitation(
            tenant_id=tid, email=email, role=role, token_hash=_sha256(token),
            invited_by=inviter.id if inviter else None, status="pending",
            expires_at=_now() + datetime.timedelta(days=INVITE_TTL_DAYS), created_at=_now(),
        )
        db.add(inv)
        db.flush()
        tenant = db.get(Tenant, _tid(tenant_id))
        link = f"{settings.app_base_url.rstrip('/')}/accept-invite?token={token}"
        mailer.send_invitation(email, tenant.name if tenant else None, role, link)
        out = _invite_dto(inv)
        if _dev_reveal():
            out["devToken"] = token
        return out


def list_invitations(tenant_id: str) -> list[dict]:
    with system_session() as db:
        _set_tenant(db, tenant_id)
        rows = db.execute(
            select(Invitation).where(Invitation.tenant_id == _tid(tenant_id)).order_by(Invitation.id.desc())
        ).scalars().all()
        return [_invite_dto(r) for r in rows]


def revoke_invitation(tenant_id: str, invite_public_id: str) -> dict:
    with system_session() as db:
        _set_tenant(db, tenant_id)
        inv = db.execute(select(Invitation).where(Invitation.public_id == invite_public_id)).scalars().first()
        if inv is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found.")
        if inv.status == "pending":
            inv.status = "revoked"
        return _invite_dto(inv)


def peek_invitation(token: str) -> dict:
    """Public: show the accept screen who was invited and to which company."""
    claims = tokens.decode_token(token, expected_type="invite")
    tid = claims.get("companyId")
    company = None
    with system_session() as db:
        if tid:
            _set_tenant(db, tid)
            inv = db.execute(select(Invitation).where(Invitation.token_hash == _sha256(token))).scalars().first()
            if inv is None or inv.status != "pending" or inv.expires_at < _now():
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invitation is invalid or has expired.")
            tenant = db.get(Tenant, uuid.UUID(tid))
            company = tenant.name if tenant else None
    return {"email": claims.get("email"), "role": claims.get("role"),
            "company": {"id": tid, "name": company}}


def accept_invitation(token: str, name: str, password: str) -> dict:
    claims = tokens.decode_token(token, expected_type="invite")
    tid, email, role = claims.get("companyId"), claims.get("email"), claims.get("role")
    with system_session() as db:
        if tid:
            _set_tenant(db, tid)
        inv = db.execute(select(Invitation).where(Invitation.token_hash == _sha256(token))).scalars().first()
        if inv is None or inv.status != "pending" or inv.expires_at < _now():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This invitation is invalid or has expired.")
        if _find_by_email(db, email):
            raise HTTPException(status.HTTP_409_CONFLICT, "That email already has an account.")
        user = User(
            tenant_id=uuid.UUID(tid), external_id=f"local:{uuid.uuid4().hex}",
            email=email.strip(), display_name=(name or email).strip(),
            is_owner=False, status="Active", role=role, is_active=True,
            email_verified=True, password_hash=hash_password(password),
        )
        db.add(user)
        db.flush()
        inv.status = "accepted"
        inv.accepted_at = _now()
        return _issue(db, user)


# --------------------------------------------------------------------------- #
# User administration (within a company)
# --------------------------------------------------------------------------- #
def _user_dto(u: User) -> dict:
    return {
        "id": str(u.public_id), "email": u.email, "name": u.display_name or u.email,
        "role": u.role, "isOwner": bool(u.is_owner), "isActive": bool(u.is_active),
        "emailVerified": bool(u.email_verified),
        "lastLogin": u.last_login.isoformat() if u.last_login else None,
    }


def list_users(tenant_id: str) -> list[dict]:
    with system_session() as db:
        _set_tenant(db, tenant_id)
        rows = db.execute(
            select(User).where(User.tenant_id == _tid(tenant_id)).order_by(User.id.asc())
        ).scalars().all()
        return [_user_dto(u) for u in rows]


def list_companies() -> list[dict]:
    """Super Admin cross-company overview. `tenants` carries no tenant_id column,
    so it's outside the RLS policy and lists in full; per-tenant counts set the
    session context so they work in dev (trusted conn) and prod alike."""
    with system_session() as db:
        _clear_tenant(db)
        tenants = db.execute(select(Tenant).order_by(Tenant.name.asc())).scalars().all()
        out: list[dict] = []
        for t in tenants:
            _set_tenant(db, t.id)
            users = db.execute(
                select(func.count()).select_from(User).where(User.tenant_id == t.id)
            ).scalar() or 0
            active = db.execute(
                select(func.count()).select_from(User)
                .where(User.tenant_id == t.id, User.is_active == True)  # noqa: E712
            ).scalar() or 0
            sub = db.execute(
                select(Subscription).where(Subscription.tenant_id == t.id)
            ).scalars().first()
            out.append({
                "id": str(t.id), "name": t.name, "slug": t.slug,
                "currency": t.base_currency_code, "status": t.status,
                "users": int(users), "activeUsers": int(active),
                "plan": sub.plan if sub else None,
                "subscriptionStatus": sub.status if sub else None,
                "seatLimit": sub.seat_limit if sub else None,
            })
        return out


def update_user(*, tenant_id: str, user_public_id: str, actor_public_id: str | None,
                role: str | None = None, is_active: bool | None = None) -> dict:
    with system_session() as db:
        _set_tenant(db, tenant_id)
        u = db.execute(select(User).where(User.public_id == user_public_id)).scalars().first()
        if u is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
        if role is not None and role != u.role:
            if u.is_owner:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "The workspace owner's role can't be changed.")
            _assignable_role(role)
            u.role = role
        if is_active is not None:
            if u.is_owner and not is_active:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "The workspace owner can't be deactivated.")
            if actor_public_id and str(u.public_id) == str(actor_public_id) and not is_active:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't deactivate your own account.")
            u.is_active = bool(is_active)
        return _user_dto(u)
