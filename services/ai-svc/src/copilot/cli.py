"""`copilot` command-line entry point."""

from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from copilot.config import REPO_ROOT, get_settings
from copilot.db.migrate import apply_migrations, harden_service_roles

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
        harden_service_roles(conn, settings)
    typer.echo(f"applied: {', '.join(applied) if applied else 'nothing (up to date)'}")


@app.command()
def simgen(
    days: int = typer.Option(30, help="Days of 1-minute data to generate."),
    seed: int = typer.Option(7, help="Random seed."),
    end: str | None = typer.Option(None, help="ISO-8601 end time with offset; default now (UTC)."),
    out: Path = typer.Option(REPO_ROOT / "data" / "ground_truth.jsonl", help="Ground-truth file."),
) -> None:
    """Generate synthetic telemetry/alarms, load them per tenant and write ground truth."""
    from copilot.simgen.generate import generate
    from copilot.simgen.loader import load, tenant_counts, write_ground_truth

    end_dt = datetime.fromisoformat(end) if end else datetime.now(UTC)
    if end_dt.tzinfo is None:
        raise typer.BadParameter("--end must include a timezone offset, e.g. 2026-10-17T08:00+00:00")
    settings = get_settings()
    result = generate(end_dt, days=days, seed=seed)
    typer.echo(f"generated {result.start.isoformat()} .. {result.end.isoformat()} "
               f"({len(result.alarms)} alarms, {len(result.ground_truth)} incidents)")
    load(result, settings)
    write_ground_truth(result, out)
    counts = tenant_counts(settings, [t.id for t in result.tenants])
    for tenant_id, c in counts.items():
        typer.echo(f"{tenant_id}: " + ", ".join(f"{k}={v}" for k, v in c.items()))
    typer.echo(f"ground truth: {out}")
    if any(c["foreign"] for c in counts.values()):
        typer.echo("ERROR: rows from another tenant were visible", err=True)
        raise typer.Exit(1)
