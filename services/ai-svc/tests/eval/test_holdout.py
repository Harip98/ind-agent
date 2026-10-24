from collections import Counter
from datetime import UTC, datetime

import pytest

from copilot.eval.dataset import DatasetError, load_dataset, write_holdout
from copilot.eval.holdout import build_holdout
from copilot.eval.score import score_case
from copilot.simgen.catalog import HOLDOUT_TENANTS
from copilot.simgen.scenarios import NOVEL_CAUSES


@pytest.fixture(scope="module")
def inputs():
    from copilot.simgen.generate import generate

    sim = generate(datetime(2026, 10, 17, 12, 52, tzinfo=UTC), days=30, seed=7)
    gt = [g.to_json() for g in sim.ground_truth]
    alarms = [{"tenant_id": a.tenant_id, "asset_id": a.asset_id, "tag": a.tag, "alarm_type": a.alarm_type,
               "raised_at": a.raised_at.isoformat()} for a in sim.alarms]
    return gt, {"start": sim.start.isoformat(), "end": sim.end.isoformat(), "days": 30, "seed": 7}, alarms


@pytest.fixture(scope="module")
def cases(inputs):
    return build_holdout(*inputs)


def test_holdout_covers_every_category(cases):
    counts = Counter(c.category for c in cases)
    assert set(counts) == {"normal", "paraphrase", "combination", "novel_fault", "stale", "injection",
                           "missing_evidence", "unauthorized", "ambiguous"}
    assert len({c.id for c in cases}) == len(cases) and all(c.split == "holdout" for c in cases)


def test_holdout_is_one_tenant_and_shares_no_incident_with_dev_or_test(cases, inputs):
    gt = {g["incident_id"]: g for g in inputs[0]}
    incidents = {c.incident_id for c in cases if c.incident_id}
    assert incidents and all(gt[i]["tenant_id"] in HOLDOUT_TENANTS for i in incidents)
    asking = {c.tenant for c in cases if c.category != "unauthorized"}
    assert asking == HOLDOUT_TENANTS


def test_holdout_has_causes_the_test_split_never_had(cases):
    causes = {c.expected_cause for c in cases if c.category == "normal"}
    assert {"BEARING_WEAR", "CAVITATION", "DRY_RUN", "SENSOR_FAILURE"} <= causes


def test_combinations_require_every_real_cause(cases):
    combos = {c.group: c for c in cases if c.category == "combination"}
    assert set(combos["CMB-01"].expected_causes) == {"BEARING_WEAR", "COOLING_RESTRICTION"}
    assert combos["CMB-02"].expected_causes == ("HX_FOULING",)  # the nuisance spike is a distractor
    assert all(len(c.acceptable_docs) == len(set(c.acceptable_docs)) for c in combos.values())


def test_novel_faults_expect_no_cause(cases):
    novel = [c for c in cases if c.category == "novel_fault"]
    assert novel and all(c.expected_cause is None and c.expected_status == "ok" for c in novel)


def test_holdout_is_frozen(cases, tmp_path):
    write_holdout(cases, tmp_path)
    assert load_dataset(tmp_path, "holdout") == cases
    with pytest.raises(DatasetError, match="frozen holdout"):
        write_holdout(cases[:-1], tmp_path)
    write_holdout(cases[:-1], tmp_path, refreeze="test")
    assert "holdout" in (tmp_path / "FREEZE_LOG.md").read_text(encoding="utf-8")
    (tmp_path / "holdout.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(DatasetError, match="hash mismatch"):
        load_dataset(tmp_path, "holdout")


def _response(*explanations):
    return {"status": "ok", "report": {"possible_explanations": [
        {"cause_code": code, "confidence": conf, "text": "", "refs": []} for code, conf in explanations],
        "recommendation": {"text": "", "refs": []}}, "citations": [], "llm": None}


def test_combination_scoring_accepts_either_cause_first_and_needs_both_in_top3(cases):
    case = next(c for c in cases if c.group == "CMB-01")
    both = score_case(case, _response(("BEARING_WEAR", "medium"), ("COOLING_RESTRICTION", "high")), None)
    assert both["top1"] and both["top3"]
    one = score_case(case, _response(("COOLING_RESTRICTION", "high")), None)
    assert one["top1"] and not one["top3"]


def test_novel_scoring_fails_a_confident_cause(cases):
    case = next(c for c in cases if c.category == "novel_fault")
    assert score_case(case, _response(), None)["novel_pass"]
    assert score_case(case, _response(("CAVITATION", "low")), None)["novel_pass"]
    assert not score_case(case, _response(("CAVITATION", "medium")), None)["novel_pass"]
    degraded = {**_response(("CAVITATION", "low")), "status": "degraded"}
    assert score_case(case, degraded, None)["novel_pass"]  # writer failures are scored by status_ok, not here
    assert not score_case(case, {"status": "needs_clarification", "report": None, "citations": [], "llm": None},
                          None)["novel_pass"]
    assert NOVEL_CAUSES
