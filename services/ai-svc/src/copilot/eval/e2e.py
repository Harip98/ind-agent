"""Resumable end-to-end runner and Markdown report."""

import json
import secrets
import statistics
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from copilot.auth import Principal
from copilot.eval.dataset import COUNTS, EvalCase
from copilot.eval.score import score_case
from copilot.pipeline import InvestigateRequest, PipelineDeps, investigate

STEPS = ("intent", "data", "analyze", "retrieve", "llm", "verify", "report")


def _done(out_path: Path) -> dict[str, dict]:
    if not out_path.exists():
        return {}
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["id"]: r for r in rows}


def _anonymous(case: EvalCase) -> Principal:
    return Principal(case.tenant, "eval", ("engineer",), "")


def run_cases(cases: list[EvalCase], deps: PipelineDeps, mode: str, out_path: Path, limit: int | None = None,
              principal_for: Callable[[EvalCase], Principal] = _anonymous) -> list[dict]:
    """Run every case not already in `out_path` (JSONL, appended as we go) and return all rows in case order.
    `principal_for` must return a principal with a real token when deps talk to the real data-svc."""
    done = _done(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    todo = [c for c in cases if c.id not in done]
    if limit is not None:
        todo = todo[:limit]
    with out_path.open("a", encoding="utf-8", newline="\n") as fh:
        for case in todo:
            principal = principal_for(case)
            req = InvestigateRequest(case.question, datetime.fromisoformat(case.as_of), mode)
            start = time.perf_counter()
            try:
                response, error = investigate(req, principal, deps, secrets.token_hex(8)), None
            except Exception as exc:  # recorded, never fatal to the run
                response, error = None, f"{type(exc).__name__}: {exc}"
            row = score_case(case, response, error)
            row["wall_ms"] = int((time.perf_counter() - start) * 1000)
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            done[case.id] = row
    return [done[c.id] for c in cases if c.id in done]


def _rate(rows: list[dict], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(sum(bool(v) for v in vals) / len(vals), 3) if vals else None


def _pct(values: list[float], q: float) -> int | None:
    s = sorted(values)
    return int(s[min(len(s) - 1, int(round(q * (len(s) - 1))))]) if s else None


def _group(rows: list[dict]) -> dict:
    llm_rows = [r for r in rows if r.get("verified") is not None]
    return {
        "n": len(rows),
        "status_acc": _rate(rows, "status_ok"),
        "top1": _rate(rows, "top1"),
        "top3": _rate(rows, "top3"),
        "citation": _rate(rows, "citation_ok"),
        "stale_in_rec": sum(bool(r["stale_in_recommendation"]) for r in rows),
        "unsafe": sum(bool(r["unsafe"]) for r in rows),
        "leaks": sum(bool(r["leak"]) for r in rows),
        "errors": sum(bool(r["error"]) for r in rows),
        "verified": _rate(llm_rows, "verified") if llm_rows else None,
        "degraded": sum(r["status"] == "degraded" for r in rows),
    }


def summarize(rows: list[dict]) -> dict:
    out = {cat: _group([r for r in rows if r["category"] == cat]) for cat in COUNTS
           if any(r["category"] == cat for r in rows)}
    out["all"] = _group(rows)
    timed = [r for r in rows if r.get("latency_ms") is not None]
    out["latency"] = {step: {"p50": _pct([r["timings"].get(step, 0) for r in timed if step in r["timings"]], 0.5),
                             "p95": _pct([r["timings"].get(step, 0) for r in timed if step in r["timings"]], 0.95)}
                      for step in STEPS}
    out["latency"]["total"] = {"p50": _pct([r["latency_ms"] for r in timed], 0.5),
                               "p95": _pct([r["latency_ms"] for r in timed], 0.95)}
    tokens = [r["tokens"] for r in rows if r.get("tokens")]
    out["tokens_mean"] = round(statistics.fmean(tokens)) if tokens else None
    return out


def _fmt(v) -> str:
    if v is None:
        return "–"
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def render_e2e_report(summary: dict, rows: list[dict], title: str, notes: list[str]) -> str:
    lines = [f"# {title}", "",
             f"**Hard gates:** cross-tenant leaks = {summary['all']['leaks']} (must be 0), "
             f"unsafe advice = {summary['all']['unsafe']} (must be 0), "
             f"superseded procedure in recommendation = {summary['all']['stale_in_rec']}.", "",
             "| Category | n | Status acc. | Top-1 cause | Top-3 cause | Citation support | Verified (LLM) | "
             "Degraded | Errors |",
             "|---|---|---|---|---|---|---|---|---|"]
    for cat in [*[c for c in COUNTS if c in summary], "all"]:
        g = summary[cat]
        lines.append(f"| {cat} | {g['n']} | {_fmt(g['status_acc'])} | {_fmt(g['top1'])} | {_fmt(g['top3'])} | "
                     f"{_fmt(g['citation'])} | {_fmt(g['verified'])} | {g['degraded']} | {g['errors']} |")
    lines += ["", "| Step | p50 ms | p95 ms |", "|---|---|---|"]
    for step, v in summary["latency"].items():
        if v["p50"] is not None:
            lines.append(f"| {step} | {v['p50']} | {v['p95']} |")
    if summary.get("tokens_mean"):
        lines += ["", f"Mean LLM tokens per request (prompt + completion): {summary['tokens_mean']}."]
    failures = [r for r in rows if not r["status_ok"] or r.get("top1") is False or r.get("citation_ok") is False
                or r["unsafe"] or r["leak"] or r["error"]]
    lines += ["", f"## Failures ({len(failures)})", ""]
    if failures:
        lines += ["| Case | Expected | Got | Causes | Recommendation docs | Error |", "|---|---|---|---|---|---|"]
        for r in failures:
            lines.append(f"| {r['id']} | {r['expected_status']} / {r['expected_cause'] or '–'} | {r['status']} | "
                         f"{', '.join(r['codes']) or '–'} | {', '.join(r['rec_docs']) or '–'} | "
                         f"{(r['error'] or '')[:80]} |")
    lines += ["", "## Method & limitations", ""] + [f"- {n}" for n in notes]
    return "\n".join(lines) + "\n"
