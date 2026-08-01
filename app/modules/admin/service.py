"""Admin business logic → frontend ScreenConfig (matches modules/admin mock shapes)."""
import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.core import User
from app.modules.admin import repository as repo
from app.modules.admin.dto import (
    ApprovalRuleCreate, ApprovalRuleUpdate, RoleCreate, RoleUpdate,
    UserCreate, UserUpdate,
)
from app.presenters.screen import list_config, text_cell

_ROLE_TONE = {"Administrator": "accent", "Owner": "accent",
              "Buyer": "neutral"}  # everything else → navy
_USER_STATUS_TONE = {"Active": "green", "Invited": "amber", "Suspended": "red"}
_RULE_TONE = {"Active": "green", "Draft": "neutral", "Paused": "amber"}
_AUDIT_TONE = {"Approved": "green", "Created": "accent", "Updated": "navy",
               "Voided": "red", "Deleted": "red", "Failed login": "red"}


def _tid(t: str | UUID) -> UUID:
    return t if isinstance(t, uuid.UUID) else uuid.UUID(str(t))


def _role_badge(role: str | None) -> str:
    return _ROLE_TONE.get(role or "", "navy")


def _human_size(n: int | None) -> str:
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.0f} KB"
    return f"{n} B"


# ---------- Users ----------

def users_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_users(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["display_name"] or r["email"], avatar=True, sub=r["email"]),
            text_cell(r["role"] or "—", badge=_role_badge(r["role"])),
            r["department"] or "—",
            r["last_active"] or "—",
            text_cell(r["status"], badge=_USER_STATUS_TONE.get(r["status"], "neutral")),
        ]
        for r in rows
    ]
    return list_config(
        columns=[{"label": "User"}, {"label": "Role"}, {"label": "Department"},
                 {"label": "Last active"}, {"label": "Status"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"name": r["display_name"], "email": r["email"],
                  "role": r["role"], "department": r["department"]} for r in rows],
        search="Search users…", action="Invite user", filters=["Role", "Status"],
    )


def create_user(session, *, tenant_id, payload: UserCreate):
    return repo.create_user(session, tenant_id=_tid(tenant_id), name=payload.name,
                            email=payload.email, role=payload.role, department=payload.department)


def update_user(session, *, public_id, payload: UserUpdate):
    return repo.update_user(session, public_id=public_id, name=payload.name,
                            email=payload.email, role=payload.role, department=payload.department)


# ---------- Roles ----------

def roles_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_roles(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["name"], strong=True),
            text_cell(str(r["users"]), align="center", mono=True),
            r["scope"] or "—",
            "—",
        ]
        for r in rows
    ]
    return list_config(
        columns=[{"label": "Role"}, {"label": "Users", "align": "center"},
                 {"label": "Scope"}, {"label": "Updated"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"name": r["name"], "scope": r["scope"]} for r in rows],
        search="Search roles…", action="New role", filters=[],
    )


def create_role(session, *, tenant_id, payload: RoleCreate):
    return repo.create_role(session, tenant_id=_tid(tenant_id), name=payload.name, scope=payload.scope)


def update_role(session, *, public_id, payload: RoleUpdate):
    return repo.update_role(session, public_id=public_id, name=payload.name, scope=payload.scope)


# ---------- Approval rules ----------

def rules_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_rules(session, limit=limit, offset=offset)
    grid = [
        [
            text_cell(r["name"], strong=True),
            r["condition"] or "—",
            r["approver"] or "—",
            text_cell(r["status"], badge=_RULE_TONE.get(r["status"], "neutral")),
        ]
        for r in rows
    ]
    return list_config(
        columns=[{"label": "Rule"}, {"label": "Condition"}, {"label": "Approver"},
                 {"label": "Status"}],
        rows=grid, total=total,
        ids=[str(r["public_id"]) for r in rows],
        records=[{"name": r["name"], "condition": r["condition"], "approver": r["approver"]}
                 for r in rows],
        search="Search rules…", action="New rule", filters=["Module"],
    )


def _rule_fields(p) -> dict:
    return {"name": p.name, "condition": p.condition, "approver": p.approver}


def create_rule(session, *, tenant_id, payload: ApprovalRuleCreate):
    return repo.create_rule(session, tenant_id=_tid(tenant_id), **_rule_fields(payload))


def update_rule(session, *, public_id, payload: ApprovalRuleUpdate):
    return repo.update_rule(session, public_id=public_id, **_rule_fields(payload))


# ---------- Document library (read-only) ----------

def doclibrary_screen(session: Session, *, limit: int = 50, offset: int = 0) -> dict:
    rows, total = repo.list_documents(session, limit=limit, offset=offset)
    # Resolve uploader names once.
    grid = []
    for r in rows:
        ext = (r["filename"].rsplit(".", 1)[-1] if "." in r["filename"] else "FILE").upper()
        dtype = (r["entity_type"] or r["module"] or "—").title()
        grid.append([
            text_cell(r["filename"], avatar=True, sub=ext),
            text_cell(dtype, badge="navy"),
            "System",
            r["created_at"].strftime("%d %b") if r["created_at"] else "—",
            text_cell(_human_size(r["size_bytes"]), align="right", mono=True),
        ])
    return list_config(
        columns=[{"label": "Document"}, {"label": "Type"}, {"label": "Owner"},
                 {"label": "Updated"}, {"label": "Size", "align": "right"}],
        rows=grid, total=total,
        search="Search documents…", action=None, filters=["Type", "Owner"],
    )


# ---------- Audit log (read-only) ----------

def audit_screen(session: Session, *, limit: int = 100) -> dict:
    rows = repo.list_audit(session, limit=limit)
    # Resolve actor ids → display names.
    actor_ids = {r["actor_id"] for r in rows if r["actor_id"] is not None}
    names: dict[int, str] = {}
    if actor_ids:
        for uid, nm, em in session.execute(
            select(User.id, User.display_name, User.email).where(User.id.in_(actor_ids))
        ):
            names[uid] = nm or em
    grid = []
    for r in rows:
        action = r["action"] or "—"
        entity = r["entity_type"] or "—"
        if r["entity_id"]:
            entity = f"{entity} · {r['entity_id']}"
        grid.append([
            text_cell(r["occurred_at"].strftime("%d %b %H:%M") if r["occurred_at"] else "—", mono=True),
            names.get(r["actor_id"], "System"),
            text_cell(action, badge=_AUDIT_TONE.get(action, "neutral")),
            entity,
            text_cell("—", mono=True),
        ])
    return list_config(
        columns=[{"label": "Time"}, {"label": "User"}, {"label": "Action"},
                 {"label": "Entity"}, {"label": "IP"}],
        rows=grid, total=len(grid),
        search="Search audit log…", action=None, filters=["User", "Entity", "Date"],
    )
