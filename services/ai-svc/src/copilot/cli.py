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
    from copilot.simgen.loader import load, tenant_counts, write_ground_truth, write_run_metadata

    end_dt = datetime.fromisoformat(end) if end else datetime.now(UTC)
    if end_dt.tzinfo is None:
        raise typer.BadParameter("--end must include a timezone offset, e.g. 2026-10-17T08:00+00:00")
    settings = get_settings()
    result = generate(end_dt, days=days, seed=seed)
    typer.echo(f"generated {result.start.isoformat()} .. {result.end.isoformat()} "
               f"({len(result.alarms)} alarms, {len(result.ground_truth)} incidents)")
    load(result, settings)  # all tenants or none; files are written only after it succeeds
    write_ground_truth(result, out)
    write_run_metadata(result, out.with_name("simgen_run.json"))
    counts = tenant_counts(settings, [t.id for t in result.tenants])
    for tenant_id, c in counts.items():
        typer.echo(f"{tenant_id}: " + ", ".join(f"{k}={v}" for k, v in c.items()))
    typer.echo(f"ground truth: {out} (run metadata: {out.with_name('simgen_run.json')})")
    if any(c["foreign"] for c in counts.values()):
        typer.echo("ERROR: rows from another tenant were visible", err=True)
        raise typer.Exit(1)


@app.command()
def devtoken(
    tenant: str = typer.Option(..., help="Tenant id, e.g. acme."),
    sub: str = typer.Option("demo-user", help="Subject (user id)."),
    ttl_minutes: int = typer.Option(60, help="Lifetime in minutes (max 60)."),
) -> None:
    """Print a short-lived development JWT for one tenant."""
    from datetime import timedelta

    from copilot.auth import issue_token

    typer.echo(issue_token(tenant, sub, get_settings().jwt_secret, ttl=timedelta(minutes=ttl_minutes)))


@app.command()
def ingest(
    path: Path = typer.Option(REPO_ROOT / "corpus", help="Corpus directory."),
    fake_embeddings: bool = typer.Option(False, help="Use the offline hashing embedder."),
) -> None:
    """Parse the corpus and write changed document versions to the knowledge base."""
    from copilot.kb.corpus import load_corpus
    from copilot.kb.embed import HashEmbedder, OllamaEmbedder
    from copilot.kb.ingest import ingest as run_ingest
    from copilot.ollama import OllamaClient

    settings = get_settings()
    embedder = HashEmbedder() if fake_embeddings else OllamaEmbedder(
        OllamaClient(settings.ollama_url, settings.ollama_timeout_s), settings.embed_model)
    docs = load_corpus(path)
    stats = run_ingest(docs, settings, embedder)
    typer.echo(f"{len(docs)} versions: {stats.written} written ({stats.chunks} chunks), "
               f"{stats.skipped} unchanged")


def _retriever(settings, fake_embeddings: bool = False):
    from copilot.kb.embed import HashEmbedder, OllamaEmbedder
    from copilot.ollama import OllamaClient
    from copilot.retrieval import Retriever
    from copilot.retrieval.rerank import CrossEncoderReranker

    embedder = HashEmbedder() if fake_embeddings else OllamaEmbedder(
        OllamaClient(settings.ollama_url, settings.ollama_timeout_s), settings.embed_model)
    return Retriever(settings, embedder, CrossEncoderReranker(settings.reranker_model))


@app.command()
def search(
    query: str,
    tenant: str = typer.Option(..., help="Tenant id."),
    model: str | None = typer.Option(None, help="Asset model filter, e.g. CP-200."),
    strategy: str = typer.Option("hybrid_rerank", help="vector | hybrid | hybrid_rerank"),
    k: int = typer.Option(5),
) -> None:
    """Search the knowledge base as a tenant."""
    for i, h in enumerate(_retriever(get_settings()).search(tenant, query, model, k, strategy), 1):
        flag = " [STALE]" if h.stale else ""
        typer.echo(f"{i}. {h.section_id}{flag}  score={h.score:.3f}  {h.title} — {h.heading}")


@app.command("eval-retrieval")
def eval_retrieval(
    strategy: str = typer.Option("all", help="all | vector | hybrid | hybrid_rerank"),
) -> None:
    """Compare retrieval strategies on eval/datasets/retrieval.jsonl and write a report."""
    import json
    from datetime import date

    from copilot.eval.retrieval_eval import REPO_DATASET, load_cases, render_report, run
    from copilot.retrieval import STRATEGIES

    strategies = list(STRATEGIES) if strategy == "all" else [strategy]
    results = run(load_cases(REPO_DATASET), _retriever(get_settings()), strategies)
    out = REPO_ROOT / "eval" / "reports" / f"{date.today().isoformat()}-retrieval"
    report = render_report(results, f"Retrieval evaluation — {date.today().isoformat()}")
    out.with_suffix(".md").write_text(report, encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(results, indent=1), encoding="utf-8")
    typer.echo(report)
    if any(r["leak"] for rows in results.values() for r in rows):
        typer.echo("ERROR: cross-tenant leak", err=True)
        raise typer.Exit(1)
