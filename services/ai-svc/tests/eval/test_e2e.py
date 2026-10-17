import json
from datetime import UTC, datetime

import pytest
from test_pipeline import make_deps

from copilot.eval.dataset import EvalCase
from copilot.eval.e2e import EvalStateError, render_e2e_report, run_cases, summarize, wilson
from copilot.simgen.generate import generate

END = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def sim():
    return generate(END, days=30, seed=7)


def _case(cid, question, status, cause=None, category="normal", tenant="acme", incident=None, **kw):
    return EvalCase(cid, "dev", category, tenant, question, END.isoformat(), status, cause, None, incident,
                    ("SOP-PUMP-07",) if cause else (), group=incident or cid, **kw)


CASES = [
    _case("N-1", "Investigate the temperature alarm on Pump 12 yesterday.", "ok", "COOLING_RESTRICTION",
          incident="INC-01"),
    _case("M-1", "Investigate the vibration alarm on Pump 12 yesterday.", "insufficient_evidence",
          category="missing_evidence"),
    _case("U-1", "Investigate the temperature alarm yesterday.", "needs_clarification", category="unauthorized",
          request_asset_id="P-20"),
]
FP = {"dataset": "abc", "mode": "template"}


def test_runner_scores_resumes_and_passes_request_fields(sim, tmp_path):
    out = tmp_path / "results.jsonl"
    rows = run_cases(CASES, make_deps(sim), "template", out, fingerprint=FP)
    assert [r["id"] for r in rows] == ["N-1", "M-1", "U-1"]
    assert all(r["status_ok"] for r in rows), rows
    assert rows[0]["top1"] and rows[0]["procedure_doc_match"] and rows[0]["wall_ms"] >= 0
    again = run_cases(CASES, make_deps(sim), "template", out, fingerprint=FP)
    assert len(again) == 3 and len(out.read_text(encoding="utf-8").splitlines()) == 3


def test_resume_refuses_a_different_fingerprint(sim, tmp_path):
    out = tmp_path / "r.jsonl"
    run_cases(CASES[:1], make_deps(sim), "template", out, fingerprint=FP)
    with pytest.raises(EvalStateError, match="dataset"):
        run_cases(CASES, make_deps(sim), "template", out, fingerprint={**FP, "dataset": "changed"})


def test_errors_are_recorded_and_can_be_retried(sim, tmp_path):
    def broken(principal, trace):
        raise RuntimeError("data-svc exploded")

    deps = make_deps(sim)
    deps.datasvc = broken
    out = tmp_path / "r.jsonl"
    rows = run_cases(CASES, deps, "template", out, limit=2, fingerprint=FP)
    assert len(rows) == 2 and all("exploded" in r["error"] for r in rows)
    rows = run_cases(CASES, make_deps(sim), "template", out, fingerprint=FP, retry_errors=True)
    assert [r["error"] for r in rows] == [None, None, None]


def test_malformed_last_line_is_skipped(sim, tmp_path):
    out = tmp_path / "r.jsonl"
    run_cases(CASES[:1], make_deps(sim), "template", out, fingerprint=FP)
    with out.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "M-1", "trunc')
    rows = run_cases(CASES, make_deps(sim), "template", out, fingerprint=FP)
    assert [r["id"] for r in rows] == ["N-1", "M-1", "U-1"]


def test_wilson_interval():
    lo, hi = wilson(15, 15)
    assert 0.79 < lo < 0.81 and hi == 1.0
    assert wilson(0, 0) == (None, None)


def test_summary_and_report(sim, tmp_path):
    rows = run_cases(CASES, make_deps(sim), "template", tmp_path / "r.jsonl", fingerprint=FP)
    s = summarize(rows, mode="template")
    a = s["all"]
    assert a["n"] == 3 and a["status_acc"]["rate"] == 1.0 and a["leaks"] == 0 and a["incidents"] == 1
    assert s["normal"]["top1"]["rate"] == 1.0 and s["missing_evidence"]["top1"] is None
    assert s["latency"]["total"]["p50"] is not None and "full" in s["latency"]
    md = render_e2e_report(s, rows, "Test report", ["note one"], mode="template")
    assert "| normal |" in md and "note one" in md and "p95" in md and "95% CI" in md
    assert "deterministic" in md
    json.dumps(s)
