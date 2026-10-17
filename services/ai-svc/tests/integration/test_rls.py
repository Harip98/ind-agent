import psycopg
import pytest

from copilot.db.migrate import apply_migrations

from .conftest import as_tenant

pytestmark = pytest.mark.integration

TENANTS = ("itest_a", "itest_b")


def _cleanup(conn: psycopg.Connection, tenant_id: str) -> None:
    conn.execute("DELETE FROM ops.assets WHERE tenant_id = %s", (tenant_id,))
    conn.execute("DELETE FROM ops.tenants WHERE id = %s", (tenant_id,))


@pytest.fixture(scope="module")
def seeded(settings):
    with psycopg.connect(settings.dsn("ops_loader")) as conn:
        for tid in TENANTS:
            with conn.transaction():
                as_tenant(conn, tid)
                _cleanup(conn, tid)
                conn.execute(
                    "INSERT INTO ops.tenants (id, name, timezone) VALUES (%s, %s, 'UTC')", (tid, tid)
                )
                conn.execute(
                    "INSERT INTO ops.assets (tenant_id, id, name, asset_model, site)"
                    " VALUES (%s, 'P-1', 'Pump 1', 'CP-200', 'Test')",
                    (tid,),
                )
    with psycopg.connect(settings.dsn("kb_writer")) as conn:
        with conn.transaction():
            as_tenant(conn, "itest_a")
            conn.execute("DELETE FROM kb.documents WHERE id LIKE %s", ("ITEST-%",))
            conn.execute(
                "INSERT INTO kb.documents (id, tenant_id, title, doc_type) VALUES"
                " ('ITEST-SHARED', NULL, 'Shared', 'sop'),"
                " ('ITEST-A', 'itest_a', 'Tenant A only', 'sop')"
            )
    yield
    with psycopg.connect(settings.dsn("ops_loader")) as conn:
        for tid in TENANTS:
            with conn.transaction():
                as_tenant(conn, tid)
                _cleanup(conn, tid)
    with psycopg.connect(settings.dsn("kb_writer")) as conn:
        with conn.transaction():
            as_tenant(conn, "itest_a")
            conn.execute("DELETE FROM kb.documents WHERE id LIKE %s", ("ITEST-%",))


def test_reader_sees_only_own_tenant(settings, seeded):
    with psycopg.connect(settings.dsn("ops_reader")) as conn:
        as_tenant(conn, "itest_a")
        rows = conn.execute(
            "SELECT DISTINCT tenant_id FROM ops.assets WHERE tenant_id LIKE 'itest_%'"
        ).fetchall()
        explicit = conn.execute("SELECT count(*) FROM ops.assets WHERE tenant_id = 'itest_b'")
        assert explicit.fetchone()[0] == 0
    assert rows == [("itest_a",)]


def test_reader_without_tenant_sees_nothing(settings, seeded):
    with psycopg.connect(settings.dsn("ops_reader")) as conn:
        assert conn.execute("SELECT count(*) FROM ops.assets").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM ops.tenants").fetchone()[0] == 0


def test_reader_cannot_write(settings, seeded):
    with psycopg.connect(settings.dsn("ops_reader")) as conn:
        as_tenant(conn, "itest_a")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO ops.tenants (id, name, timezone) VALUES ('itest_a', 'x', 'UTC')")


def test_loader_cannot_write_other_tenant(settings, seeded):
    with psycopg.connect(settings.dsn("ops_loader")) as conn:
        as_tenant(conn, "itest_a")
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            conn.execute(
                "INSERT INTO ops.assets (tenant_id, id, name, asset_model, site)"
                " VALUES ('itest_b', 'P-9', 'Pump 9', 'CP-200', 'Test')"
            )


def test_kb_reader_has_no_access_to_ops(settings):
    with psycopg.connect(settings.dsn("kb_reader")) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM ops.assets LIMIT 1")


def test_ops_reader_has_no_access_to_kb(settings):
    with psycopg.connect(settings.dsn("ops_reader")) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM kb.documents LIMIT 1")


def test_kb_shared_docs_visible_tenant_docs_isolated(settings, seeded):
    with psycopg.connect(settings.dsn("kb_reader")) as conn:
        as_tenant(conn, "itest_b")
        ids = {r[0] for r in conn.execute("SELECT id FROM kb.documents WHERE id LIKE %s", ("ITEST-%",))}
    assert ids == {"ITEST-SHARED"}


def test_migrations_are_idempotent(settings):
    with psycopg.connect(settings.dsn("app_owner"), autocommit=True) as conn:
        assert apply_migrations(conn) == []
