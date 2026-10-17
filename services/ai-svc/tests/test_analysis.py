from datetime import UTC, datetime

import pytest
from simwindows import asset_info, primary_alarm, windows_for

from copilot.analysis import TagWindows, analyze
from copilot.causes import load_signatures
from copilot.datasvc import AlarmInfo, SensorWindow
from copilot.simgen.generate import generate

END = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def sim():
    return generate(END, days=30, seed=7)


@pytest.fixture(scope="module")
def roles():
    return load_signatures().roles


def _analysis(sim, roles, incident_id):
    gt = next(g for g in sim.ground_truth if g.incident_id == incident_id)
    info = asset_info(gt.tenant_id, gt.asset_id)
    alarm = primary_alarm(sim, gt)
    return analyze(info, alarm, windows_for(sim, gt.tenant_id, gt.asset_id, alarm.raised_at),
                   roles[info.model], "Asia/Kolkata")


def test_cooling_restriction_features(sim, roles):
    a = _analysis(sim, roles, "INC-01")
    f = a.features
    assert f["bearing_temp.rising"] and f["cw_return_temp.rising"]
    assert f["flow.flat"] and f["current.flat"] and not f["alarm.short"]
    assert a.observations[0].kind == "alarm" and a.observations[0].tag == "TT-101"
    assert all(o.id == f"O{i}" for i, o in enumerate(a.observations, 1))
    assert set(a.series) == {"TT-101", "TT-102", "FT-101", "PT-101", "IT-101", "VT-101"}


def test_dry_run_is_sudden(sim, roles):
    f = _analysis(sim, roles, "INC-04").features
    assert f["flow.sudden_drop"] and f["pressure.falling"]


def test_sensor_failure_is_bad_data(sim, roles):
    a = _analysis(sim, roles, "INC-07")
    assert a.features["alarmed.bad_data"]
    dq = [o for o in a.observations if o.kind == "data_quality"]
    assert dq and dq[0].values["gap_minutes"] >= 60 and dq[0].values["flat_minutes"] >= 120


def test_nuisance_spike_is_short_without_trend(sim, roles):
    f = _analysis(sim, roles, "INC-08").features
    assert f["alarm.short"] and f["alarmed.no_trend"] and f["related.all_flat"]


def test_sensor_drift_rises_over_days_alone(sim, roles):
    f = _analysis(sim, roles, "INC-03").features
    assert f["alarmed.rising"] and f["related.all_flat"] and f["alarmed.over_days"]


def test_observation_numbers_are_rounded_text(sim, roles):
    a = _analysis(sim, roles, "INC-01")
    trend = next(o for o in a.observations if o.tag == "TT-102" and o.kind == "trend")
    assert "rose" in trend.text and "degC" in trend.text
    assert isinstance(trend.values["delta"], float)


def test_missing_data_is_unknown_not_error(roles):
    info = asset_info("acme", "P-12")
    t = datetime(2026, 10, 16, 3, 41, tzinfo=UTC)
    empty = SensorWindow("P-12", "TT-101", "degC", t, t, 60, ())
    windows = {s.tag: TagWindows(empty, empty) for s in info.sensors}
    alarm = AlarmInfo("x", "P-12", "TT-101", "high_temp", "medium", t, None, 85.1, 85.0, "m")
    a = analyze(info, alarm, windows, roles["CP-200"], "Asia/Kolkata")
    assert a.features["alarmed.bad_data"]
    assert any("no readings" in o.text for o in a.observations)
