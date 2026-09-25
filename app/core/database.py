"""Database engines and the tenant session-context plumbing.

Two engines:
  * ``app_engine``    — runtime user, subject to Row-Level Security.
  * ``system_engine`` — provisioning user (erp_system), exempt from RLS.

Tenant isolation: before running any tenant-scoped query we set
``app.tenant_id`` on the connection. Because the pool reuses connections,
we defensively CLEAR the context on every checkout so a stale tenant can
never leak into the next request.
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
    cur.execute("RESET app.tenant_id")
    cur.execute("RESET app.rls_bypass")
    cur.close()


def _set_tenant(session: Session, tenant_id: str) -> None:
    session.execute(
        text("SET app.tenant_id = :tid"),
        {"tid": str(tenant_id)},
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
    """Privileged session for provisioning / pre-auth lookups. Opts out of RLS
    via app.rls_bypass so it is exempt even when the connection isn't the
    erp_system principal."""
    session = SystemSession()
    try:
        session.execute(text("SET app.rls_bypass = 'true'"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
