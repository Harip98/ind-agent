"""`copilot` command-line entry point."""

import psycopg
import typer

from copilot.config import get_settings
from copilot.db.migrate import apply_migrations, set_role_passwords

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Industrial troubleshooting copilot tooling."""


@app.command()
def migrate() -> None:
    """Apply pending database migrations and set service role passwords."""
    settings = get_settings()
    with psycopg.connect(settings.dsn("app_owner"), autocommit=True) as conn:
        applied = apply_migrations(conn)
        set_role_passwords(conn, settings)
    typer.echo(f"applied: {', '.join(applied) if applied else 'nothing (up to date)'}")
