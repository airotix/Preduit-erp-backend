"""Role → permission mapping (single source of truth for authorization).

Roles are fixed platform-wide. A permission is a `module.action` string; `"*"`
means all permissions. `require_permission(...)` in security.py checks these.
"""

# Canonical roles.
SUPER_ADMIN = "Super Admin"   # platform operator — cross-company (is_platform_admin)
ADMIN = "Admin"               # company owner — everything within their company
MANAGER = "Manager"
MERCHANDISER = "Merchandiser"
ACCOUNTANT = "Accountant"
USER_OVERVIEW = "User Overview"   # read-only across the app
LOGISTICS = "Logistics / Inventory"

# Super Admin is platform-only; the rest are the assignable, per-company roles.
ROLES = [SUPER_ADMIN, ADMIN, MANAGER, MERCHANDISER, ACCOUNTANT, USER_OVERVIEW, LOGISTICS]

# Module read/write permission helpers.
_MODULES = ["dashboard", "catalog", "inventory", "sales", "procurement",
            "finance", "production", "quality", "shipments", "ai"]


def _rw(*modules: str) -> set[str]:
    perms: set[str] = set()
    for m in modules:
        perms.add(f"{m}.read")
        perms.add(f"{m}.write")
    return perms


def _ro(*modules: str) -> set[str]:
    return {f"{m}.read" for m in modules}


ROLE_PERMISSIONS: dict[str, set[str]] = {
    SUPER_ADMIN: {"*"},
    ADMIN: {"*"},  # full access within the company (incl. admin.users / admin.settings)
    MANAGER: _rw("dashboard", "catalog", "inventory", "sales", "procurement",
                 "production", "quality", "shipments") | _ro("finance", "ai"),
    MERCHANDISER: _rw("catalog", "inventory", "sales", "ai") | _ro("dashboard", "production"),
    ACCOUNTANT: _rw("finance") | _ro("dashboard", "sales", "procurement"),
    USER_OVERVIEW: _ro(*_MODULES),  # read-only visibility across every module
    LOGISTICS: _rw("inventory", "shipments", "procurement", "production") | _ro("dashboard", "catalog"),
}


def permissions_for(role: str | None, is_platform_admin: bool = False) -> list[str]:
    """Resolve a role (and platform flag) to a concrete permission list."""
    if is_platform_admin or role == SUPER_ADMIN:
        return ["*"]
    return sorted(ROLE_PERMISSIONS.get(role or "", set()))


def has_permission(perms: list[str], needed: str) -> bool:
    return "*" in perms or needed in perms
