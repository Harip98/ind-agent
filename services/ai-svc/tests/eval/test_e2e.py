import json
from datetime import UTC, datetime

import pytest
from test_pipeline import make_deps

from copilot.eval.dataset import EvalCase
from copilot.eval.e2e import render_e2e_report, run_cases, summarize
from copilot.simgen.generate import generate

END = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def sim():
    return generate(END, days=30, seed=7)


def _case(cid, question, status, cause=None, category="normal", tenant="acme"):
    return EvalCase(cid, "dev", category, tenant, question, END.isoformat(), status, cause, None, None,
                    ("SOP-PUMP-07",) if cause else ())


CASES = [
    _case("N-1", "Investigate the temperature alarm on Pump 12 yesterday.", "ok", "COOLING_RESTRICTION"),
    _case("M-1", "Investigate the vibration alarm on Pump 12 yesterday.", "insufficient_evidence",
          category="missing_evidence"),
    _case("U-1", "Investigate the temperature alarm on Pump 20 yesterday.", "needs_clarification",
          category="unauthorized"),
]


def test_runner_scores_and_resumes(sim, tmp_path):
    out = tmp_path / "results.jsonl"
    rows = run_cases(CASES, make_deps(sim), "template", out)
    assert [r["id"] for r in rows] == ["N-1", "M-1", "U-1"]
    assert all(r["status_ok"] for r in rows), rows
    assert rows[0]["top1"] and rows[0]["citation_ok"]
    again = run_cases(CASES, make_deps(sim), "template", out)
    assert len(again) == 3 and len(out.read_text(encoding="utf-8").splitlines()) == 3


def test_limit_and_errors_are_recorded(sim, tmp_path):
    def broken(principal, trace):
        raise RuntimeError("data-svc exploded")

    deps = make_deps(sim)
    deps.datasvc = broken
    rows = run_cases(CASES, deps, "template", tmp_path / "r.jsonl", limit=2)
    assert len(rows) == 2 and all("exploded" in r["error"] for r in rows)


def test_summary_and_report(sim, tmp_path):
    rows = run_cases(CASES, make_deps(sim), "template", tmp_path / "r.jsonl")
    s = summarize(rows)
    assert s["all"]["n"] == 3 and s["all"]["status_acc"] == 1.0 and s["all"]["leaks"] == 0
    assert s["normal"]["top1"] == 1.0 and s["missing_evidence"]["top1"] is None
    md = render_e2e_report(s, rows, "Test report", ["note one"])
    assert "| normal |" in md and "note one" in md and "p95" in md
    json.dumps(s)
