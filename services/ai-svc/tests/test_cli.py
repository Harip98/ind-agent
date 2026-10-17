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
