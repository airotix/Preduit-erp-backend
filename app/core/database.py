"""Database engines and the tenant SESSION_CONTEXT plumbing (plan §2).

Two engines:
  * ``app_engine``    — runtime user, subject to Row-Level Security.
  * ``system_engine`` — provisioning user (erp_system), exempt from RLS.

Tenant isolation: before running any tenant-scoped query we set
``SESSION_CONTEXT('tenant_id')`` on the connection. Because the pool reuses
connections, we defensively CLEAR the context on every checkout so a stale
tenant can never leak into the next request.
"""
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

app_engine = create_engine(settings.app_database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
system_engine = create_engine(settings.system_database_url, pool_pre_ping=True, pool_size=2, max_overflow=2)

AppSession = sessionmaker(bind=app_engine, autoflush=False, expire_on_commit=False)
SystemSession = sessionmaker(bind=system_engine, autoflush=False, expire_on_commit=False)


@event.listens_for(app_engine, "checkout")
def _clear_tenant_on_checkout(dbapi_conn, conn_record, conn_proxy):  # noqa: ANN001
    """Reset tenant context whenever a pooled connection is handed out."""
    cur = dbapi_conn.cursor()
    cur.execute("EXEC sp_set_session_context @key=N'tenant_id', @value=NULL")
    cur.close()


def _set_tenant(session: Session, tenant_id: str) -> None:
    session.execute(
        text("EXEC sp_set_session_context @key=N'tenant_id', @value=:tid, @read_only=0"),
        {"tid": tenant_id},
    )


def get_db(tenant_id: str) -> Iterator[Session]:
    """Yield an RLS-scoped session bound to ``tenant_id`` (FastAPI dependency)."""
    session = AppSession()
    try:
        _set_tenant(session, tenant_id)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def system_session() -> Iterator[Session]:
    """Privileged session for provisioning (bypasses RLS). Use sparingly."""
    session = SystemSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
