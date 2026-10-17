"""Apply numbered SQL migrations from db/migrations exactly once each."""

import hashlib
import re
from pathlib import Path

import psycopg
from psycopg import sql

from copilot.config import REPO_ROOT, Settings

MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
_NAME = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")
_SERVICE_ROLES = ("ops_reader", "ops_loader", "kb_reader", "kb_writer")
_LOCK_ID = 7_202_610  # pg_advisory_lock key: one migrate run at a time


class MigrationChecksumError(RuntimeError):
    """An already-applied migration file was edited."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def check_checksums(migrations_dir: Path, applied: dict[str, str | None]) -> None:
    for version, path in discover(migrations_dir):
        recorded = applied.get(version)
        if recorded and recorded != _sha(path):
            raise MigrationChecksumError(f"{version} was edited after being applied")


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
    conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
            " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        conn.execute("ALTER TABLE public.schema_migrations ADD COLUMN IF NOT EXISTS sha256 text")
        applied = dict(conn.execute("SELECT version, sha256 FROM public.schema_migrations").fetchall())
        check_checksums(migrations_dir, applied)
        newly_applied = []
        for version, path in discover(migrations_dir):
            if version in applied:
                if applied[version] is None:
                    conn.execute("UPDATE public.schema_migrations SET sha256 = %s WHERE version = %s",
                                 (_sha(path), version))
                continue
            with conn.transaction():
                conn.execute(path.read_bytes().decode("utf-8"))
                conn.execute("INSERT INTO public.schema_migrations (version, sha256) VALUES (%s, %s)",
                             (version, _sha(path)))
            newly_applied.append(version)
        return newly_applied
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))


def harden_service_roles(conn: psycopg.Connection, settings: Settings) -> None:
    """Set service role passwords and re-assert they can never bypass row-level security."""
    for role in _SERVICE_ROLES:
        conn.execute(
            sql.SQL("ALTER ROLE {} NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(settings.password_for(role))
            )
        )
