from typer.testing import CliRunner

from copilot.cli import app


def test_simgen_rejects_unparseable_end_cleanly():
    result = CliRunner().invoke(app, ["simgen", "--end", "yesterday"])
    assert result.exit_code == 2
    assert "--end" in result.output


def test_simgen_rejects_too_few_days_cleanly():
    result = CliRunner().invoke(app, ["simgen", "--days", "5", "--end", "2026-10-17T00:00+00:00"])
    assert result.exit_code == 2
    assert "days" in result.output


def test_eval_and_ask_name_agent_mode():
    result = CliRunner().invoke(app, ["eval", "--mode", "bogus"])
    assert result.exit_code == 2 and "agent" in result.output
    assert "agent" in CliRunner().invoke(app, ["ask", "--help"]).output


def test_eval_compare_reads_saved_rows(tmp_path):
    import json

    row = {"id": "N-1", "category": "normal", "incident_id": "INC-01", "expected_status": "ok",
           "expected_cause": "X", "status": "ok", "status_ok": True, "top1": True, "top3": True,
           "procedure_doc_match": True, "stale_pass": None, "verified": None, "first_attempt_ok": None,
           "stale_in_recommendation": False, "stale_flagged": False, "unsafe": False, "injection_followed": False,
           "leak": [], "error": None, "llm_failed": False, "verifier_categories": {}, "timings": {}, "tokens": None,
           "llm_seconds": None, "wall_ms": 40, "response": None}
    for mode in ("template", "llm"):
        (tmp_path / f"e2e-test-{mode}.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["eval-compare", "--split", "test", "--modes", "template,llm,agent",
                                      "--reports-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "| Metric | template | llm |" in result.output and "agent: no results" in result.output


def test_ask_prints_a_failed_agent_run(monkeypatch):
    from datetime import UTC, datetime

    import copilot.api
    from test_pipeline import make_deps

    from copilot.llm import FakeToolLLM
    from copilot.simgen.generate import generate

    end = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
    deps = make_deps(generate(end, days=30, seed=7))
    deps.tool_llm = FakeToolLLM([{"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "list_assets", "arguments": {}}}]}] * 6)
    monkeypatch.setattr(copilot.api, "default_deps", lambda: deps)
    result = CliRunner().invoke(app, ["ask", "Why is Pump 12 hot?", "--tenant", "acme", "--mode", "agent",
                                      "--as-of", end.isoformat()])
    assert result.exit_code == 0, result.output
    assert "agent_failed" in result.output and "list_assets" in result.output
