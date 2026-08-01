"""Audit trail helper.

Writes rows into dbo.audit_log (created in V001). Runs on the tenant-scoped
session, so RLS keeps each tenant's audit isolated. Reads power the real
"Activity" tab on detail pages.
"""
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def write_audit(session: Session, *, tenant_id: UUID | str, action: str,
                entity_type: str, entity_id: str,
                detail: str | None = None, actor_id: int | None = None) -> None:
    session.execute(
        text(
            "INSERT INTO dbo.audit_log (tenant_id, actor_id, action, entity_type, entity_id, detail) "
            "VALUES (:t, :actor, :action, :etype, :eid, :detail)"
        ),
        {"t": str(tenant_id), "actor": actor_id, "action": action,
         "etype": entity_type, "eid": entity_id, "detail": detail},
    )


def list_audit(session: Session, *, entity_type: str, entity_id: str) -> list[dict]:
    rows = session.execute(
        text(
            "SELECT action, entity_type, entity_id, detail, occurred_at "
            "FROM dbo.audit_log WHERE entity_type = :etype AND entity_id = :eid "
            "ORDER BY occurred_at DESC"
        ),
        {"etype": entity_type, "eid": entity_id},
    ).mappings().all()
    return [dict(r) for r in rows]
