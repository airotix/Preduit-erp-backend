"""Admin data access (tenant-scoped by RLS)."""
import uuid
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.admin import ApprovalRule
from app.models.core import Role, User
from app.models.document import Document


# ---------- Users ----------

def list_users(session, *, limit, offset):
    stmt = (
        select(User.public_id, User.display_name, User.email, User.role,
               User.department, User.last_active, User.status)
        .order_by(User.id).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(select(func.count()).select_from(User)).scalar_one()
    return rows, total


def create_user(session: Session, *, tenant_id: UUID, name, email, role, department) -> User:
    u = User(tenant_id=tenant_id, external_id=f"invite:{uuid.uuid4()}", email=email,
             display_name=name, role=role, department=department, status="Invited")
    session.add(u)
    session.flush()
    session.refresh(u)
    return u


def update_user(session: Session, *, public_id, name, email, role, department) -> User | None:
    u = session.execute(select(User).where(User.public_id == public_id)).scalar_one_or_none()
    if u is None:
        return None
    u.display_name, u.email, u.role, u.department = name, email, role, department
    session.flush()
    session.refresh(u)
    return u


# ---------- Roles ----------

def list_roles(session, *, limit, offset):
    counts = {
        rid: c for rid, c in session.execute(
            text("SELECT role_id, COUNT(*) AS c FROM dbo.user_roles GROUP BY role_id")
        )
    }
    stmt = select(Role.public_id, Role.id, Role.name, Role.scope).order_by(Role.name).limit(limit).offset(offset)
    rows = []
    for r in session.execute(stmt):
        m = dict(r._mapping)
        m["users"] = counts.get(m["id"], 0)
        rows.append(m)
    total = session.execute(select(func.count()).select_from(Role)).scalar_one()
    return rows, total


def create_role(session: Session, *, tenant_id: UUID, name, scope) -> Role:
    role = Role(tenant_id=tenant_id, name=name, scope=scope, is_system=False)
    session.add(role)
    session.flush()
    session.refresh(role)
    return role


def update_role(session: Session, *, public_id, name, scope) -> Role | None:
    role = session.execute(select(Role).where(Role.public_id == public_id)).scalar_one_or_none()
    if role is None:
        return None
    role.name, role.scope = name, scope
    session.flush()
    session.refresh(role)
    return role


# ---------- Approval rules ----------

def list_rules(session, *, limit, offset):
    stmt = (
        select(ApprovalRule.public_id, ApprovalRule.name, ApprovalRule.condition,
               ApprovalRule.approver, ApprovalRule.status)
        .where(ApprovalRule.is_deleted == False)  # noqa: E712
        .order_by(ApprovalRule.id).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(ApprovalRule).where(ApprovalRule.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


def _apply_rule(r: ApprovalRule, *, name, condition, approver) -> None:
    r.name, r.condition, r.approver = name, condition, approver


def create_rule(session: Session, *, tenant_id: UUID, **fields) -> ApprovalRule:
    r = ApprovalRule(tenant_id=tenant_id, status="Active")
    _apply_rule(r, **fields)
    session.add(r)
    session.flush()
    session.refresh(r)
    return r


def update_rule(session: Session, *, public_id, **fields) -> ApprovalRule | None:
    r = session.execute(
        select(ApprovalRule).where(ApprovalRule.public_id == public_id,
                                   ApprovalRule.is_deleted == False)  # noqa: E712
    ).scalar_one_or_none()
    if r is None:
        return None
    _apply_rule(r, **fields)
    session.flush()
    session.refresh(r)
    return r


# ---------- Document library ----------

def list_documents(session, *, limit, offset):
    stmt = (
        select(Document.filename, Document.module, Document.entity_type, Document.content_type,
               Document.size_bytes, Document.created_at)
        .where(Document.is_deleted == False)  # noqa: E712
        .order_by(Document.id.desc()).limit(limit).offset(offset)
    )
    rows = [dict(r._mapping) for r in session.execute(stmt)]
    total = session.execute(
        select(func.count()).select_from(Document).where(Document.is_deleted == False)  # noqa: E712
    ).scalar_one()
    return rows, total


# ---------- Audit log ----------

def list_audit(session, *, limit):
    rows = session.execute(
        text("SELECT TOP (:lim) occurred_at, actor_id, action, entity_type, entity_id, detail "
             "FROM dbo.audit_log ORDER BY occurred_at DESC"),
        {"lim": limit},
    ).mappings().all()
    return [dict(r) for r in rows]
