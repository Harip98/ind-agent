"""Apply numbered SQL migrations from db/migrations exactly once each."""

import re
from pathlib import Path

import psycopg
from psycopg import sql

from copilot.config import REPO_ROOT, Settings

MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
_NAME = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")
_SERVICE_ROLES = ("ops_reader", "ops_loader", "kb_reader", "kb_writer")


def discover(migrations_dir: Path) -> list[tuple[str, Path]]:
    found = []
    for path in sorted(migrations_dir.iterdir()):
        if path.suffix != ".sql":
            continue
        if not _NAME.match(path.name):
            raise ValueError(f"bad migration filename: {path.name}")
        found.append((path.stem, path))
    return found


def apply_migrations(conn: psycopg.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations, each in its own transaction. `conn` must be autocommit."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
        " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM public.schema_migrations")}
    newly_applied = []
    for version, path in discover(migrations_dir):
        if version in applied:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO public.schema_migrations (version) VALUES (%s)", (version,))
        newly_applied.append(version)
    return newly_applied


def harden_service_roles(conn: psycopg.Connection, settings: Settings) -> None:
    """Set service role passwords and re-assert they can never bypass row-level security."""
    for role in _SERVICE_ROLES:
        conn.execute(
            sql.SQL("ALTER ROLE {} NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(settings.password_for(role))
            )
        )
