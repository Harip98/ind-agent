import json
from datetime import UTC, datetime

from copilot.simgen.generate import generate
from copilot.simgen.loader import write_ground_truth


def test_write_ground_truth_jsonl(tmp_path):
    result = generate(datetime(2026, 10, 17, 8, 0, tzinfo=UTC))
    path = tmp_path / "nested" / "gt.jsonl"
    write_ground_truth(result, path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(result.ground_truth)
    assert {r["incident_id"] for r in rows} >= {"INC-01", "INC-11"}
