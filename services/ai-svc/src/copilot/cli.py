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

    try:
        end_dt = datetime.fromisoformat(end) if end else datetime.now(UTC)
    except ValueError:
        raise typer.BadParameter("must be ISO-8601, e.g. 2026-10-17T08:00+00:00", param_hint="--end")
    if end_dt.tzinfo is None:
        raise typer.BadParameter("must include a timezone offset, e.g. 2026-10-17T08:00+00:00",
                                 param_hint="--end")
    settings = get_settings()
    try:
        result = generate(end_dt, days=days, seed=seed)
    except ValueError as exc:
        raise typer.BadParameter(f"{exc} (increase --days)", param_hint="--days") from exc
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
    stats = run_ingest(docs, settings, embedder, prune=True)
    typer.echo(f"{len(docs)} versions: {stats.written} written ({stats.chunks} chunks), "
               f"{stats.skipped} unchanged, {stats.removed} removed")


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
    strategy: str = typer.Option("hybrid", help="vector | hybrid | hybrid_rerank"),
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


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the HTTP API (POST /investigate)."""
    import uvicorn

    from copilot.api import create_app

    uvicorn.run(create_app(), host=host, port=port)


def _default_as_of():
    import json
    from datetime import UTC, datetime

    meta = REPO_ROOT / "data" / "simgen_run.json"
    if meta.exists():
        return datetime.fromisoformat(json.loads(meta.read_text(encoding="utf-8"))["end"])
    return datetime.now(UTC)


@app.command()
def ask(
    question: str,
    tenant: str = typer.Option(..., help="Tenant id (a dev token is minted for it)."),
    as_of: str | None = typer.Option(None, help="ISO-8601 'now' for relative times; default: simgen end."),
    mode: str = typer.Option("llm", help="llm | template"),
    strategy: str | None = typer.Option(None, help="vector | hybrid | hybrid_rerank"),
    asset_id: str | None = typer.Option(None, help="Answer an asset clarification."),
    alarm_id: str | None = typer.Option(None, help="Answer an event clarification."),
    as_json: bool = typer.Option(False, "--json", help="Print the raw JSON response."),
) -> None:
    """Investigate a question end to end (in-process pipeline, real data-svc and Ollama)."""
    import json
    import secrets
    from datetime import datetime

    from copilot.api import default_deps
    from copilot.auth import issue_token, verify_token
    from copilot.pipeline import InvestigateRequest, investigate

    settings = get_settings()
    as_of_dt = datetime.fromisoformat(as_of) if as_of else _default_as_of()
    principal = verify_token(issue_token(tenant, "cli-user", settings.jwt_secret), settings.jwt_secret)
    result = investigate(InvestigateRequest(question, as_of_dt, mode, strategy, asset_id, alarm_id),
                         principal, default_deps(),
                         secrets.token_hex(8))
    if as_json:
        typer.echo(json.dumps(result, indent=2, default=str))
        return
    typer.echo(f"status: {result['status']}   (as of {as_of_dt.isoformat()})")
    if result["clarification"]:
        typer.echo(result["clarification"]["message"])
        for o in result["clarification"]["options"]:
            hint = f"--alarm-id {o['alarm_id']}" if "alarm_id" in o else f"--asset-id {o['asset_id']}"
            typer.echo(f"  - {o['label']}   ({hint})")
        return
    rep = result["report"]
    typer.echo(f"\nSUMMARY\n  {rep['summary']}\n\nOBSERVATIONS")
    for o in rep["observations"]:
        typer.echo(f"  [{o['id']}] {o['text']}")
    typer.echo("\nPOSSIBLE EXPLANATIONS")
    for e in rep["possible_explanations"]:
        typer.echo(f"  - {e['label']} ({e['confidence']}, score {e['score']}): {e['text']} {e['refs']}")
    if rep["recommendation"]:
        typer.echo(f"\nRECOMMENDATION\n  {rep['recommendation']['text']} {rep['recommendation']['refs']}")
    for item in rep["insufficient_evidence"]:
        typer.echo(f"  ! {item}")
    typer.echo("\nCITATIONS")
    for c in result["citations"]:
        typer.echo(f"  [{c['ref']}] {c['doc_id']} v{c['version']} §{c['section']} {c['heading']}"
                   + ("  [SUPERSEDED]" if c["stale"] else ""))
    typer.echo(f"\ntimings_ms: {result['timings_ms']}   llm: {result['llm']}")


@app.command("eval-build")
def eval_build(
    refreeze: str | None = typer.Option(None, help="Reason for changing the frozen test split (logged)."),
) -> None:
    """Generate eval/datasets/{dev,test}.jsonl from the anchored simulator run (data/simgen_run.json)."""
    import json
    from datetime import datetime

    from copilot.eval.dataset import DatasetError, build_cases, split_cases, write_dataset
    from copilot.simgen.generate import generate

    meta = json.loads((REPO_ROOT / "data" / "simgen_run.json").read_text(encoding="utf-8"))
    anchor = {k: meta[k] for k in ("start", "end", "days", "seed")}
    sim = generate(datetime.fromisoformat(anchor["end"]), days=anchor["days"], seed=anchor["seed"])
    gt = [g.to_json() for g in sim.ground_truth]
    on_disk = [json.loads(line) for line in (REPO_ROOT / "data" / "ground_truth.jsonl").read_text(encoding="utf-8")
               .splitlines() if line.strip()]
    if [g["incident_id"] for g in on_disk] != [g["incident_id"] for g in gt] or on_disk != json.loads(json.dumps(gt)):
        raise typer.BadParameter("data/ground_truth.jsonl does not match the anchored simulator run; re-run simgen")
    alarms = [{"tenant_id": a.tenant_id, "asset_id": a.asset_id, "tag": a.tag, "alarm_type": a.alarm_type,
               "raised_at": a.raised_at.isoformat()} for a in sim.alarms]
    dev, test = split_cases(build_cases(gt, anchor, alarms))
    try:
        write_dataset(dev, test, anchor, REPO_ROOT / "eval" / "datasets", refreeze=refreeze)
    except DatasetError as exc:
        raise typer.BadParameter(str(exc), param_hint="--refreeze") from exc
    typer.echo(f"dev {len(dev)}, test {len(test)} cases -> eval/datasets (anchor {anchor['end']}, seed {anchor['seed']})")


EVAL_NOTES = [
    "Questions are generated deterministically from the simulator run (`copilot eval-build`), labelled "
    "`review: generated`; they have NOT been reviewed by a person, and no manual spot check of answers was done yet.",
    "The test split is a group split: 5 incidents (5 different root causes) plus a third of the missing-evidence, "
    "unauthorized and ambiguous groups never appear in dev. It was not used for tuning (hash in eval/datasets/test.sha256, "
    "re-freezes logged in FREEZE_LOG.md).",
    "The simulator's faults and the fault signatures were written by the same author, so root-cause accuracy on this "
    "data shows internal consistency of the pipeline, not real-plant validity. Effective sample size is the number of "
    "incidents, not the number of questions.",
    "'Procedure doc match' checks the recommended document against a per-cause list at document level; it is not a "
    "section-level or human judgement of citation quality.",
]
LLM_NOTES = [
    "Hardware: laptop RTX 3050 Ti 4 GB, Ryzen 7 4800H; LLM qwen2.5:7b-instruct via Ollama (partially CPU-offloaded); "
    "retrieval strategy hybrid; data-svc and Postgres in Docker on the same machine.",
]


def _sha_files(paths) -> str:
    import hashlib

    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:16]


def _fingerprint(split: str, mode: str, settings) -> dict:
    import subprocess

    from copilot.causes import SIGNATURES_PATH

    def git(*args):
        try:
            return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
        except OSError:
            return "unknown"

    datasets = REPO_ROOT / "eval" / "datasets"
    return {
        "dataset": _sha_files([datasets / f"{split}.jsonl", datasets / "anchor.json"]),
        "code": git("rev-parse", "--short", "HEAD") + ("+dirty" if git("status", "--porcelain", "--", "services") else ""),
        "corpus": _sha_files((REPO_ROOT / "corpus").rglob("*.md")),
        "signatures": _sha_files([SIGNATURES_PATH]),
        "model": settings.llm_model if mode != "template" else None,
        "strategy": settings.retrieval_strategy,
        "mode": mode,
    }


@app.command("eval")
def eval_e2e(
    split: str = typer.Option("dev", help="dev | test"),
    mode: str = typer.Option("template", help="template | llm"),
    limit: int | None = typer.Option(None, help="Run at most N new cases (resumable)."),
    fresh: bool = typer.Option(False, help="Discard earlier results for this split and mode."),
    retry_errors: bool = typer.Option(False, help="Re-run cases whose earlier attempt ended in an error."),
) -> None:
    """Run the end-to-end evaluation against the live stack and write eval/reports/e2e-<split>-<mode>.{jsonl,md}."""
    from datetime import date

    from copilot.api import default_deps
    from copilot.auth import issue_token, verify_token
    from copilot.eval.dataset import check_anchor, load_dataset
    from copilot.eval.e2e import EvalStateError, render_e2e_report, run_cases, summarize

    if split not in ("dev", "test") or mode not in ("template", "llm"):
        raise typer.BadParameter("split must be dev|test and mode template|llm")
    datasets = REPO_ROOT / "eval" / "datasets"
    check_anchor(datasets, REPO_ROOT / "data" / "simgen_run.json")
    cases = load_dataset(datasets, split)
    settings = get_settings()
    out = REPO_ROOT / "eval" / "reports" / f"e2e-{split}-{mode}.jsonl"
    if fresh:
        for f in (out, out.with_suffix(".meta.json")):
            f.unlink(missing_ok=True)

    def principal_for(case):
        return verify_token(issue_token(case.tenant, "eval-runner", settings.jwt_secret), settings.jwt_secret)

    try:
        rows = run_cases(cases, default_deps(), mode, out, limit, principal_for,
                         fingerprint=_fingerprint(split, mode, settings), retry_errors=retry_errors)
    except EvalStateError as exc:
        raise typer.BadParameter(str(exc)) from exc
    summary = summarize(rows, mode)
    title = f"End-to-end evaluation — {split} split, {mode} mode ({len(rows)}/{len(cases)} cases, {date.today()})"
    notes = EVAL_NOTES + (LLM_NOTES if mode != "template" else [])
    out.with_suffix(".md").write_text(render_e2e_report(summary, rows, title, notes, mode), encoding="utf-8")
    a = summary["all"]
    rate = lambda v: "-" if v is None else f"{v['k']}/{v['n']}"  # noqa: E731
    typer.echo(f"{len(rows)}/{len(cases)} cases | status {rate(a['status_acc'])} | top-1 {rate(a['top1'])} | "
               f"procedure {rate(a['procedure'])} | stale pass {rate(a['stale_pass'])} | verified {rate(a['verified'])} | "
               f"leaks {a['leaks']} | unsafe {a['unsafe']} | injection {a['injection_followed']} | errors {a['errors']} | "
               f"p50 {summary['latency']['total']['p50']} ms")
    typer.echo(f"report: {out.with_suffix('.md')}")
