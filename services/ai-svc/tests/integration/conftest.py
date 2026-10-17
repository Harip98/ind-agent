import psycopg
import pytest
from psycopg import sql

from copilot.config import Settings, get_settings
from copilot.db.migrate import apply_migrations, harden_service_roles

TEST_DB = "indagent_test"


def as_tenant(conn: psycopg.Connection, tenant_id: str) -> None:
    """Scope the current transaction to `tenant_id`."""
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))


@pytest.fixture(scope="session")
def settings() -> Settings:
    """A dedicated, migrated database so integration tests never touch dev data."""
    base = get_settings()
    try:
        with psycopg.connect(base.dsn("app_owner"), autocommit=True) as conn:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB,)).fetchone():
                conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB)))
    except psycopg.OperationalError as exc:
        pytest.fail(f"Postgres not reachable - run `docker compose up -d`: {exc}")
    s = base.model_copy(update={"pg_db": TEST_DB})
    with psycopg.connect(s.dsn("app_owner"), autocommit=True) as conn:
        apply_migrations(conn)
        harden_service_roles(conn, s)
    return s
