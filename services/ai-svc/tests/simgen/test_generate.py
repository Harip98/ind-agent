from datetime import UTC, datetime, timedelta

import pytest

from copilot.analysis import LONG_BEFORE, SHORT_AFTER
from copilot.simgen.faults import RECOVERY_MINUTES
from copilot.simgen.generate import generate

END = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def result():
    return generate(END, days=30, seed=7)


def test_window_and_shapes(result):
    assert result.end == END
    assert (result.end - result.start).days == 30
    for series in result.series.values():
        for values in series.values.values():
            assert len(values) == 30 * 1440


def test_every_incident_raises_an_alarm_on_its_primary_tag(result):
    for gt in result.ground_truth:
        tags = {a["tag"] for a in gt.alarms}
        assert gt.primary_tag in tags, gt.incident_id


def test_every_alarm_belongs_to_exactly_one_incident_or_one_combo(result):
    for alarm in result.alarms:
        owners = [
            gt.combo or gt.incident_id
            for gt in result.ground_truth
            if any(
                a["tag"] == alarm.tag and a["raised_at"] == alarm.raised_at.isoformat()
                for a in gt.alarms
            )
            and gt.tenant_id == alarm.tenant_id
            and gt.asset_id == alarm.asset_id
        ]
        assert len(set(owners)) == 1, alarm


def test_sensor_drift_alarms_only_on_the_drifting_tag(result):
    for gt in result.ground_truth:
        if gt.cause_code == "SENSOR_DRIFT":
            assert {a["tag"] for a in gt.alarms} == {"TT-101"}


def test_demo_incident_alarm_is_yesterday_for_acme(result):
    gt = next(g for g in result.ground_truth if g.incident_id == "INC-01")
    assert (gt.tenant_id, gt.asset_id, gt.cause_code) == ("acme", "P-12", "COOLING_RESTRICTION")
    assert any(a["alarm_type"] == "high_temp" and a["tag"] == "TT-101" for a in gt.alarms)


def test_deterministic_for_same_inputs(result):
    again = generate(END, days=30, seed=7)
    assert again.alarms == result.alarms


def test_ground_truth_json_is_serialisable(result):
    row = result.ground_truth[0].to_json()
    assert row["synthetic"] is True
    assert isinstance(row["start"], str)


def test_naive_end_rejected():
    with pytest.raises(ValueError, match="timezone"):
        generate(datetime(2026, 10, 17), days=30)


def test_too_few_days_rejected():
    with pytest.raises(ValueError, match="outside the simulated window"):
        generate(END, days=5)


def test_unrelated_incidents_on_one_asset_stay_out_of_each_others_analysis_window(result):
    """Another fault inside an alarm's 48 h baseline would be an undeclared confounder in that case's evidence.
    Only incidents that share a combo id overlap on purpose."""
    recovery = timedelta(minutes=RECOVERY_MINUTES)
    for a in result.ground_truth:
        alarm = min(datetime.fromisoformat(x["raised_at"]) for x in a.alarms if x["tag"] == a.primary_tag)
        lo, hi = alarm - LONG_BEFORE, alarm + SHORT_AFTER
        for b in result.ground_truth:
            if b is a or (b.tenant_id, b.asset_id) != (a.tenant_id, a.asset_id) or (a.combo and a.combo == b.combo):
                continue
            assert b.end + recovery < lo or b.start > hi, (a.incident_id, b.incident_id)
