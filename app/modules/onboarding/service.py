"""Self-serve tenant provisioning (plan §6).

Runs under the privileged system principal (RLS-exempt) inside ONE transaction,
so a half-created tenant can never exist. Steps: create tenant → subscription →
default roles → owner user → link owner role → seed baseline settings.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import text

from app.core.database import system_session
from app.core.security import Principal
from app.models.core import Role, Subscription, Tenant, User
from app.modules.onboarding.dto import CreateOrgRequest, CreateOrgResponse

# The canonical role set for every tenant. "Admin" is the top role (full
# access) that the organization creator is linked to.
_DEFAULT_ROLES = [
    ("Admin", "Full access to the organization", True),
    ("Manager", "Operations oversight and approvals", True),
    ("Merchandiser", "Catalog and sales", True),
    ("Accountant", "Finance and accounting", True),
    ("User Overview", "Read-only dashboards and reports", True),
    ("Logistics / Inventory", "Inventory, procurement and shipments", True),
]


def create_organization(principal: Principal, req: CreateOrgRequest) -> CreateOrgResponse:
    with system_session() as db:
        # Slug uniqueness (friendly error before hitting the DB constraint).
        exists = db.execute(
            text("SELECT 1 FROM dbo.tenants WHERE slug = :s"), {"s": req.slug}
        ).first()
        if exists:
            raise ValueError("slug_taken")

        tenant = Tenant(
            id=uuid.uuid4(),  # GUID PK is not IDENTITY — assign explicitly
            name=req.name, slug=req.slug,
            base_currency_code=req.base_currency_code.upper(),
            region="primary", status="Active",
        )
        db.add(tenant)
        db.flush()  # tenant.id available for FKs below

        db.add(Subscription(
            tenant_id=tenant.id, plan="trial", status="trialing", seat_limit=5,
        ))

        roles: dict[str, Role] = {}
        for name, desc, is_system in _DEFAULT_ROLES:
            role = Role(tenant_id=tenant.id, name=name, description=desc, is_system=is_system)
            db.add(role)
            roles[name] = role
        db.flush()

        owner = User(
            tenant_id=tenant.id, external_id=principal.external_id,
            email=principal.email or "", display_name=principal.email,
            is_owner=True, status="Active",
        )
        db.add(owner)
        db.flush()

        # Link the creator → Admin role, and grant the Admin role every permission.
        db.execute(
            text("INSERT INTO dbo.user_roles (tenant_id, user_id, role_id) VALUES (:t,:u,:r)"),
            {"t": tenant.id, "u": owner.id, "r": roles["Admin"].id},
        )
        db.execute(
            text(
                "INSERT INTO dbo.role_permissions (tenant_id, role_id, permission_id) "
                "SELECT :t, :r, id FROM dbo.permissions"
            ),
            {"t": tenant.id, "r": roles["Admin"].id},
        )

        # Baseline settings + trial window.
        db.execute(
            text("INSERT INTO dbo.system_settings (tenant_id, [key], value) VALUES (:t, 'onboarded_at', :v)"),
            {"t": tenant.id, "v": datetime.now(timezone.utc).isoformat()},
        )

        # NOTE (integration TODO): write tenant.id back to the user's Entra
        # External ID profile (custom attribute) via Graph so the next token
        # carries the tenant_id claim.

        return CreateOrgResponse(
            tenant_id=str(tenant.id), slug=tenant.slug, owner_email=owner.email,
        )
