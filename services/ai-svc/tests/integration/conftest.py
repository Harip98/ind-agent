import psycopg
import pytest

from copilot.config import Settings, get_settings


def as_tenant(conn: psycopg.Connection, tenant_id: str) -> None:
    """Scope the current transaction to `tenant_id`."""
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))


@pytest.fixture(scope="session")
def settings() -> Settings:
    s = get_settings()
    try:
        psycopg.connect(s.dsn("app_owner"), connect_timeout=3).close()
    except psycopg.OperationalError as exc:
        pytest.fail(f"Postgres not reachable - run `docker compose up -d` and `copilot migrate`: {exc}")
    return s
