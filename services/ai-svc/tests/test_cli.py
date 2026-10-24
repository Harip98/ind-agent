import re

from typer.testing import CliRunner

from copilot.cli import app

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(output: str) -> str:
    """CLI error text as plain words: no colour codes (rich forces them when GITHUB_ACTIONS is set) and no box
    drawing, so a wrapped message reads as one line."""
    return " ".join(_ANSI.sub("", output).replace("│", " ").split())


def test_simgen_rejects_unparseable_end_cleanly():
    result = CliRunner().invoke(app, ["simgen", "--end", "yesterday"])
    assert result.exit_code == 2
    assert "--end" in _flat(result.output)


def test_simgen_rejects_too_few_days_cleanly():
    result = CliRunner().invoke(app, ["simgen", "--days", "5", "--end", "2026-10-17T00:00+00:00"])
    assert result.exit_code == 2
    assert "days" in _flat(result.output)


def test_eval_and_ask_name_agent_mode():
    result = CliRunner().invoke(app, ["eval", "--mode", "bogus"])
    assert result.exit_code == 2 and "agent" in _flat(result.output)
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


def test_eval_compare_summarizes_a_tagged_run_by_its_base_mode(monkeypatch, tmp_path):
    import copilot.eval.e2e as e2e

    seen = []
    monkeypatch.setattr(e2e, "load_rows", lambda path: [{"id": "x"}])
    monkeypatch.setattr(e2e, "summarize", lambda rows, mode: seen.append(mode) or {})
    monkeypatch.setattr(e2e, "render_comparison", lambda summaries: "table")
    result = CliRunner().invoke(app, ["eval-compare", "--modes", "llm-qwen7b,agent-v2", "--reports-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert seen == ["llm", "agent"]


def test_dev_docker_is_refused_outside_the_image(monkeypatch):
    from pathlib import Path

    from copilot.cli import in_container

    monkeypatch.delenv("COPILOT_CONTAINER", raising=False)
    result = CliRunner().invoke(app, ["serve", "--dev-docker"])
    assert result.exit_code == 2 and "only runs inside the ai-svc image" in _flat(result.output)
    monkeypatch.setenv("COPILOT_CONTAINER", "1")
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert not in_container()  # the image's env flag alone is not enough without the runtime's marker file
    monkeypatch.setattr(Path, "exists", lambda self: str(self).replace("\\", "/") == "/.dockerenv")
    assert in_container()


def test_dev_docker_does_not_take_a_host():
    result = CliRunner().invoke(app, ["serve", "--dev-docker", "--host", "127.0.0.1"])
    assert result.exit_code == 2 and "always binds 0.0.0.0" in _flat(result.output)


def test_ask_prints_a_failed_agent_run(monkeypatch):
    from datetime import UTC, datetime

    from test_pipeline import make_deps

    import copilot.api
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


def test_llm_notes_name_the_model_that_ran():
    from copilot.cli import llm_notes

    assert "qwen2.5:3b-instruct" in llm_notes("qwen2.5:3b-instruct", "hybrid")[0]
    assert "7b" not in llm_notes("qwen2.5:3b-instruct", "hybrid")[0]


def test_default_model_fits_the_4gb_gpu():
    from copilot.config import Settings

    assert Settings.model_fields["llm_model"].default == "qwen2.5:3b-instruct"
