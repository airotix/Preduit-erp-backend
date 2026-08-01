"""AI Insights data access — snapshot cache of the engine's responses (RLS-scoped)."""
import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.ai import AiSnapshot, AiSyncState


def get_snapshot(session: Session, *, kind: str, scope: str = "") -> AiSnapshot | None:
    return session.execute(
        select(AiSnapshot).where(AiSnapshot.kind == kind, AiSnapshot.scope == scope)
    ).scalar_one_or_none()


def upsert_snapshot(session: Session, *, tenant_id: UUID, kind: str, scope: str, data: str) -> AiSnapshot:
    row = get_snapshot(session, kind=kind, scope=scope)
    now = datetime.datetime.utcnow()
    if row is None:
        row = AiSnapshot(tenant_id=tenant_id, kind=kind, scope=scope, data=data, synced_at=now)
        session.add(row)
    else:
        row.data = data
        row.synced_at = now
    session.flush()
    return row


def delete_snapshots(session: Session, *, kinds: list[str]) -> None:
    """Drop cached snapshots for the given kinds so the next read re-fetches."""
    if not kinds:
        return
    session.execute(delete(AiSnapshot).where(AiSnapshot.kind.in_(kinds)))
    session.flush()


def get_sync_state(session: Session) -> AiSyncState | None:
    return session.execute(select(AiSyncState)).scalars().first()


def upsert_sync_state(session: Session, *, tenant_id: UUID, status: str, message: str | None,
                      synced: bool) -> AiSyncState:
    row = get_sync_state(session)
    if row is None:
        row = AiSyncState(tenant_id=tenant_id)
        session.add(row)
    row.status = status
    row.message = message
    if synced:
        row.last_synced_at = datetime.datetime.utcnow()
    session.flush()
    return row
