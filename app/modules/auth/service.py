"""Self-managed authentication logic.

Login / registration / refresh run on the privileged system session (RLS-exempt)
because there's no tenant context until a token is issued.
"""
import datetime
import hashlib
import json
import logging
import re
import secrets
import uuid

import bcrypt
from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, text

from app.core.config import get_settings
from app.core.database import system_session
from app.core import mailer
from app.core.roles import ADMIN, ROLES, SUPER_ADMIN, permissions_for
from app.models.core import (EmailVerification, Invitation, PasswordReset,
                             RefreshToken, Role, Subscription, Tenant, User)
from app.modules.auth import tokens

settings = get_settings()

# Flow lifetimes.
CODE_TTL_MIN = 15
RESET_TTL_MIN = 30
INVITE_TTL_DAYS = 7
MAX_VERIFY_ATTEMPTS = 5


def _now() -> datetime.datetime:
    # Timezone-aware UTC. Postgres TIMESTAMPTZ columns come back aware; comparing
    # them to naive datetimes raises TypeError (verify-email / lockout / invites).
    return datetime.datetime.now(datetime.timezone.utc)


def _as_utc(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _tid(tenant_id) -> uuid.UUID:
    # Coerce to UUID for ORM column comparisons/inserts. These tenant filters are
    # required for correctness in prod, where the erp_system session is RLS-exempt.
    return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))


def _dev_reveal() -> bool:
    # When SMTP isn't configured in local/dev, return OTP codes and invite
    # links in the API response so the flows are testable without inbox access.
    # Never reveal codes when ENV!=dev, even if SMTP is down.
    if settings.env != "dev":
        return False
    if settings.dev_auth_bypass:
        return True
    from app.core import mailer as _mailer
    return not _mailer.is_configured()


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
    db.execute(text("SET app.tenant_id = :tid"),
               {"tid": str(tenant_id)})


def _clear_tenant(db) -> None:
    db.execute(text("RESET app.tenant_id"))


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
                    "setupComplete": bool(tenant.setup_complete) if tenant else True,
                    "enabledModules": _get_enabled_modules(db, user.tenant_id) if user.tenant_id else None},
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
    ms = int(_as_utc(locked_until).timestamp() * 1000)
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


def _users_by_email(db, email: str) -> list:
    """Every user row for an email across all tenants (one person can own many
    businesses). In prod the system principal is RLS-exempt so one scan sees all;
    in local dev we walk each tenant."""
    _clear_tenant(db)
    rows = db.execute(
        select(User).where(func.lower(User.email) == email.strip().lower())
    ).scalars().all()
    if rows:
        return list(rows)
    found = []
    for tid in db.execute(select(Tenant.id)).scalars().all():
        _set_tenant(db, tid)
        u = _find_by_email(db, email)
        if u is not None:
            found.append(u)
    _clear_tenant(db)
    return found


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


def _business_list(db, users: list) -> list[dict]:
    out = []
    for u in users:
        t = db.get(Tenant, u.tenant_id)
        out.append({"businessId": str(u.tenant_id), "name": (t.name if t else "—"),
                    "role": u.role})
    # Stable, name-sorted.
    return sorted(out, key=lambda b: (b["name"] or "").lower())


def login(email: str, password: str, business_name: str | None = None) -> dict:
    with system_session() as db:
        users = _users_by_email(db, email)   # all businesses this email belongs to
        # Verify the password against the account (all a person's rows share it).
        verified = [u for u in users if u.is_active and verify_password(password, u.password_hash)]
        if not verified:
            locked = next((u for u in users if u.locked_until and _as_utc(u.locked_until) > _now()), None)
            if locked is not None:
                _set_tenant(db, locked.tenant_id)
                _raise_locked(locked.locked_until)
            for u in users:
                if u.is_active:
                    _set_tenant(db, u.tenant_id)
                    _register_failed_login(db, u)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

        # Single business → sign straight in. Several → the business name on the
        # login form selects which one.
        if len(verified) == 1:
            user = verified[0]
        else:
            wanted = (business_name or "").strip().lower()
            if not wanted:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "This email has multiple businesses — enter your business name to sign in.")
            matches = [u for u in verified
                       if (db.get(Tenant, u.tenant_id).name or "").strip().lower() == wanted]
            if not matches:
                raise HTTPException(status.HTTP_404_NOT_FOUND,
                                    f"No business named “{business_name.strip()}” for this account.")
            user = matches[0]

        _set_tenant(db, user.tenant_id)
        user.failed_logins = 0
        user.locked_until = None
        user.last_login = _now()

        if not user.email_verified:
            code = _create_email_code(db, user)
            mailer.send_verification_code(user.email, code)
            out: dict = {"requiresVerification": True, "email": user.email}
            if _dev_reveal():
                out["devVerifyCode"] = code
            return out

        return _issue(db, user)


def list_businesses_for(email: str) -> list[dict]:
    """Every business owned by the signed-in user's email (for the switcher)."""
    with system_session() as db:
        return _business_list(db, _users_by_email(db, email))


def switch_business(*, email: str, business_id: str) -> dict:
    """Issue fresh tokens for another business owned by the same email."""
    with system_session() as db:
        users = _users_by_email(db, email)
        user = next((u for u in users
                     if str(u.tenant_id) == business_id and u.is_active), None)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Business not found for this account")
        _set_tenant(db, user.tenant_id)
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
        if row.expires_at is None or _as_utc(row.expires_at) < _now():
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
    """Register a new business (ERP/tenant). One person (email) may own several
    businesses — but not two with the same name. To add a business under an
    existing email, the existing account's password must be supplied."""
    with system_session() as db:
        existing = _users_by_email(db, email)
        _clear_tenant(db)
        name = company_name.strip()

        additional = bool(existing)
        if additional:
            # Adding another business to an existing account: authenticate as the
            # existing owner and block a duplicate business name.
            owner_row = next((u for u in existing if u.is_active), existing[0])
            if not verify_password(password, owner_row.password_hash):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "This email already has an account — use your existing password to add another business.")
            for u in existing:
                t = db.get(Tenant, u.tenant_id)
                if t is not None and (t.name or "").strip().lower() == name.lower():
                    raise HTTPException(status.HTTP_409_CONFLICT,
                                        f"You already have a business named “{name}”.")

        slug = _slugify(company_name, db)
        tid = uuid.uuid4()
        _set_tenant(db, tid)   # so tenant/subscription/user inserts pass the block predicate
        db.add(Tenant(id=tid, name=name, slug=slug,
                      base_currency_code=(currency or "EUR").upper()[:3],
                      region="primary", status="Active"))
        db.flush()
        db.add(Subscription(tenant_id=tid, plan="trial", status="trialing", seat_limit=5))
        owner = User(
            tenant_id=tid, external_id=f"local:{uuid.uuid4().hex}",
            email=email.strip(), display_name=owner_name.strip() or email,
            is_owner=True, status="Active", role=ADMIN, is_active=True,
            password_hash=hash_password(password),
            # A returning owner is already verified; only first-time emails verify.
            email_verified=True if additional and existing[0].email_verified else False,
        )
        db.add(owner)
        db.flush()
        if additional and owner.email_verified:
            return _issue(db, owner)   # existing verified account → straight in
        code = _create_email_code(db, owner)   # seed the sign-up verification OTP
        mailer.send_verification_code(email.strip(), code)
        issued = _issue(db, owner)
        if _dev_reveal():
            issued["devVerifyCode"] = code
        return issued


def _get_enabled_modules(db, tenant_id) -> list[str] | None:
    """The company's chosen module set from the setup wizard, or None if it
    was never set (pre-existing tenants) — callers treat None as "show every
    module" so this stays backwards compatible."""
    row = db.execute(
        text("SELECT value FROM system_settings WHERE tenant_id=:t AND key='enabled_modules'"),
        {"t": str(tenant_id)},
    ).first()
    if row is None or not row[0]:
        return None
    try:
        val = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    return val if isinstance(val, list) else None


def _set_enabled_modules(db, tenant_id, modules: list[str]) -> None:
    """Idempotent upsert of the enabled-modules list into system_settings."""
    db.execute(text("DELETE FROM system_settings WHERE tenant_id=:t AND key='enabled_modules'"),
               {"t": str(tenant_id)})
    db.execute(text("INSERT INTO system_settings (tenant_id, key, value) VALUES (:t, 'enabled_modules', :v)"),
               {"t": str(tenant_id), "v": json.dumps(modules or [])})


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
        _set_enabled_modules(db, tenant_id, modules)
        user = None
        if actor_public_id:
            user = db.execute(select(User).where(User.public_id == actor_public_id)).scalars().first()
        return _profile(db, user) if user else {"company": {"id": str(tenant_id), "name": tenant.name,
                                                            "currency": tenant.base_currency_code,
                                                            "setupComplete": True,
                                                            "enabledModules": _get_enabled_modules(db, tenant_id)}}


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
        "bankName": t.bank_name or "",
        "bankAccount": t.bank_account or "",
        "bankIban": t.bank_iban or "",
        "bankSwift": t.bank_swift or "",
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
        prof = _profile_from_tenant(tenant)
        prof["enabledModules"] = _get_enabled_modules(db, tenant_id)
        return prof


def save_company_profile(tenant_id: str, p: dict) -> dict:
    # Validate contact/web formats server-side (mirrors the frontend checks).
    from app.core.validation import clean_email, clean_phone, clean_url
    try:
        clean_email(p.get("businessEmail"))
        clean_phone(p.get("phone"))
        clean_phone(p.get("supportLine"))
        clean_url(p.get("website"))
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
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
        t.bank_name = _clean(p.get("bankName"))
        t.bank_account = _clean(p.get("bankAccount"))
        t.bank_iban = _clean(p.get("bankIban"))
        t.bank_swift = _clean(p.get("bankSwift"))
        db.flush()
        if isinstance(p.get("enabledModules"), list):
            _set_enabled_modules(db, tenant_id, p["enabledModules"])
        prof = _profile_from_tenant(t)
        prof["enabledModules"] = _get_enabled_modules(db, tenant_id)
        return prof


def ensure_local_dev_accounts() -> dict:
    """DEV ONLY: idempotent seed so local login always works without a manual
    bootstrap call. Ensures a default tenant (if needed) plus:
      - tenant owner  → Admin@12345  (email verified)
      - superadmin@preduit.local → Super@12345  (platform admin, verified)
    Safe to call on every startup; only creates/updates what is missing or
    out of date. Never runs outside ENV=dev."""
    if settings.env != "dev":
        return {"skipped": True}

    made: list[dict] = []
    with system_session() as db:
        _clear_tenant(db)
        tid_uuid: uuid.UUID | None = None
        if settings.dev_tenant_id:
            try:
                tid_uuid = uuid.UUID(settings.dev_tenant_id)
            except ValueError:
                logging.getLogger("uvicorn.error").warning(
                    "DEV_TENANT_ID is not a valid GUID — ignoring for local seed.")
                tid_uuid = None

        tenant = db.get(Tenant, tid_uuid) if tid_uuid else None
        if tenant is None:
            # Prefer an existing tenant; otherwise create the local Dev Co.
            tenant = db.execute(select(Tenant).order_by(Tenant.name.asc())).scalars().first()
            if tenant is None:
                tid_uuid = tid_uuid or uuid.uuid4()
                slug = _slugify("Dev Co", db)
                tenant = Tenant(
                    id=tid_uuid, name="Dev Co", slug=slug,
                    base_currency_code="USD", region="primary", status="Active",
                    setup_complete=True,
                )
                db.add(tenant)
                db.flush()
                db.add(Subscription(tenant_id=tid_uuid, plan="trial",
                                    status="trialing", seat_limit=5))
                made.append({"tenant": "Dev Co", "id": str(tid_uuid)})
            else:
                tid_uuid = tenant.id

        _set_tenant(db, tid_uuid)

        # Owner for the demo tenant (optional — only if one already exists or we just created it).
        owner = db.execute(
            select(User).where(User.tenant_id == tid_uuid, User.is_owner == True)  # noqa: E712
        ).scalars().first()
        if owner is None:
            owner = User(
                tenant_id=tid_uuid, external_id=f"local:{uuid.uuid4().hex}",
                email=(settings.dev_email or "dev@preduit.local").strip(),
                display_name="Dev Owner", is_owner=True, status="Active",
                role=ADMIN, is_active=True, email_verified=True,
                password_hash=hash_password("Admin@12345"),
            )
            db.add(owner)
            made.append({"email": owner.email, "password": "Admin@12345", "role": ADMIN,
                         "action": "created"})
        else:
            owner.password_hash = hash_password("Admin@12345")
            owner.role = ADMIN
            owner.is_active = True
            owner.email_verified = True
            owner.locked_until = None
            owner.failed_logins = 0
            made.append({"email": owner.email, "password": "Admin@12345", "role": ADMIN,
                         "action": "reset"})

        # Platform Super Admin — always present and login-ready.
        sa = db.execute(
            select(User).where(func.lower(User.email) == "superadmin@preduit.local")
        ).scalars().first()
        if sa is None:
            # Cross-tenant lookup may miss under RLS in some local setups; create fresh.
            sa = User(
                tenant_id=tid_uuid, external_id=f"local:{uuid.uuid4().hex}",
                email="superadmin@preduit.local", display_name="Super Admin",
                is_owner=False, status="Active", role=SUPER_ADMIN,
                is_platform_admin=True, is_active=True, email_verified=True,
                password_hash=hash_password("Super@12345"),
            )
            db.add(sa)
            made.append({"email": "superadmin@preduit.local", "password": "Super@12345",
                         "role": SUPER_ADMIN, "action": "created"})
        else:
            sa.password_hash = hash_password("Super@12345")
            sa.role = SUPER_ADMIN
            sa.is_platform_admin = True
            sa.is_active = True
            sa.email_verified = True
            sa.locked_until = None
            sa.failed_logins = 0
            made.append({"email": "superadmin@preduit.local", "password": "Super@12345",
                         "role": SUPER_ADMIN, "action": "reset"})

    return {"accounts": made}


def dev_bootstrap() -> dict:
    """HTTP-facing alias for ensure_local_dev_accounts (kept for /auth/dev/bootstrap)."""
    return ensure_local_dev_accounts()


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
        active = [r for r in rows if _as_utc(r.expires_at) >= _now()]
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
        if rec is None or _as_utc(rec.expires_at) < _now():
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
            if inv is None or inv.status != "pending" or _as_utc(inv.expires_at) < _now():
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
        if inv is None or inv.status != "pending" or _as_utc(inv.expires_at) < _now():
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


def admin_create_company(*, owner_name: str, email: str, password: str,
                         company_name: str, country: str | None, city: str | None,
                         currency: str, tax_registration: str | None,
                         modules: list[str], invites: list[dict]) -> dict:
    """Super Admin: provision a complete workspace (what the self-serve signup +
    setup stepper used to do) without touching the caller's own session.

    Creates tenant + trial subscription + owner, then applies the setup payload
    (business details, modules — marks setup complete) and sends team invites.
    The owner's email is left unverified for a brand-new address, so they prove
    it with the emailed code on first sign-in. An email that already owns a
    workspace keeps its existing password / verification state."""
    name = company_name.strip()
    email = email.strip()
    with system_session() as db:
        existing = _users_by_email(db, email)
        _clear_tenant(db)
        for u in existing:
            t = db.get(Tenant, u.tenant_id)
            if t is not None and (t.name or "").strip().lower() == name.lower():
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    f"{email} already has a workspace named “{name}”.")

        slug = _slugify(name, db)
        tid = uuid.uuid4()
        _set_tenant(db, tid)
        db.add(Tenant(id=tid, name=name, slug=slug,
                      base_currency_code=(currency or "EUR").upper()[:3],
                      region="primary", status="Active"))
        db.flush()
        db.add(Subscription(tenant_id=tid, plan="trial", status="trialing", seat_limit=5))
        prior = next((u for u in existing if u.is_active), existing[0] if existing else None)
        owner = User(
            tenant_id=tid, external_id=f"local:{uuid.uuid4().hex}",
            email=email, display_name=owner_name.strip() or email,
            is_owner=True, status="Active", role=ADMIN, is_active=True,
            password_hash=prior.password_hash if prior else hash_password(password),
            email_verified=bool(prior and prior.email_verified),
        )
        db.add(owner)
        db.flush()
        owner_public_id = str(owner.public_id)
        existing_account = prior is not None
    # Tenant + owner are committed; now apply the stepper payload.
    complete_company_setup(
        tenant_id=str(tid), actor_public_id=owner_public_id, company_name=name,
        country=country, city=city, currency=currency,
        tax_registration=tax_registration, modules=modules,
    )
    invited: list[dict] = []
    skipped: list[dict] = []
    for inv in invites:
        addr = (inv.get("email") or "").strip()
        if not addr:
            continue
        try:
            invited.append(create_invitation(
                tenant_id=str(tid), inviter_public_id=owner_public_id,
                email=addr, role=inv.get("role") or ""))
        except HTTPException as exc:
            skipped.append({"email": addr, "reason": str(exc.detail)})
    return {
        "company": {"id": str(tid), "name": name, "slug": slug},
        "owner": {"email": email, "name": owner_name.strip() or email,
                  "existingAccount": existing_account,
                  "emailVerified": bool(prior and prior.email_verified)},
        "invited": invited, "skipped": skipped,
    }


def delete_company(company_id: str) -> dict:
    tid = _tid(company_id)
    with system_session() as db:
        tenant = db.execute(select(Tenant).where(Tenant.id == tid)).scalars().first()
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Company not found.")
        name = tenant.name
        tables = db.execute(text(
            "SELECT table_name FROM information_schema.columns "
            "WHERE column_name = 'tenant_id' AND table_schema = 'public' "
            "AND table_name != 'tenants'"
        )).scalars().all()
        # Disable FK checks, delete all tenant data, re-enable.
        db.execute(text("SET session_replication_role = 'replica'"))
        try:
            for tbl in tables:
                db.execute(text(f'DELETE FROM "{tbl}" WHERE tenant_id = :tid'), {"tid": str(tid)})
            db.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": str(tid)})
        finally:
            db.execute(text("SET session_replication_role = 'origin'"))
    return {"deleted": True, "name": name}


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
