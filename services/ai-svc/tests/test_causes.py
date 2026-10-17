from datetime import UTC, datetime

import pytest
from simwindows import asset_info, primary_alarm, windows_for

from copilot.analysis import analyze
from copilot.causes import CauseSignature, SignatureSet, load_signatures, match
from copilot.simgen.generate import generate

END = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
INCIDENTS = [f"INC-{n:02d}" for n in (1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 13, 14, 15, 16, 17)]


@pytest.fixture(scope="module")
def sim():
    return generate(END, days=30, seed=7)


def test_signature_file_is_well_formed():
    sigs = load_signatures()
    assert set(sigs.roles) == {"CP-200", "CL-50", "AC-75"}
    codes = {c.code for m in sigs.causes.values() for c in m} | {c.code for c in sigs.generic}
    assert codes == {"COOLING_RESTRICTION", "BEARING_WEAR", "SENSOR_DRIFT", "DRY_RUN", "CAVITATION",
                     "SENSOR_FAILURE", "HX_FOULING", "COOLER_FAN_FAILURE", "NUISANCE_SPIKE"}


def test_scoring_require_support_exclude():
    sig = CauseSignature("X", "x", "d", ("a",), ("b", "c"), ("z",), "q")
    sigs = SignatureSet({"M": {}}, {"M": [sig]}, [])
    refs = {"a": ["O1"], "b": ["O2"]}
    assert match(sigs, "M", {"a": True, "b": True, "c": False}, refs)[0].score == pytest.approx(0.75)
    assert match(sigs, "M", {"a": True, "b": True, "c": False}, refs)[0].refs == ("O1", "O2")
    assert match(sigs, "M", {"a": False, "b": True}, refs) == []
    assert match(sigs, "M", {"a": True, "z": True}, refs) == []


@pytest.mark.parametrize("incident", INCIDENTS)
def test_top_candidate_matches_ground_truth(sim, incident):
    sigs = load_signatures()
    gt = next(g for g in sim.ground_truth if g.incident_id == incident)
    info = asset_info(gt.tenant_id, gt.asset_id)
    alarm = primary_alarm(sim, gt)
    a = analyze(info, alarm, windows_for(sim, gt.tenant_id, gt.asset_id, alarm.raised_at),
                sigs.roles[info.model], "UTC")
    candidates = match(sigs, info.model, a.features, a.feature_refs)
    assert candidates, incident
    assert candidates[0].code == gt.cause_code, [(c.code, c.score) for c in candidates]
    assert all(r.startswith("O") for r in candidates[0].refs)
