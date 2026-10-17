from datetime import UTC, datetime

import numpy as np
import pytest

from copilot.simgen.catalog import ASSETS, MODELS
from copilot.simgen.faults import PROFILES, activation, apply_fault
from copilot.simgen.scenarios import INCIDENTS, resolve_start
from copilot.simgen.signals import normal_series

START = datetime(2026, 9, 1, tzinfo=UTC)
N = 6 * 1440
I0 = 1440


def _series(model_code: str):
    return normal_series(MODELS[model_code], START, N, np.random.default_rng(0))


def test_activation_ramps_holds_and_recovers():
    r = activation(100, i0=10, ramp_n=10, hold_n=5, recovery_n=10)
    assert r[9] == 0.0
    assert r[19] == pytest.approx(1.0)
    assert (r[20:25] == 1.0).all()
    assert r[34] == pytest.approx(0.0)
    assert (r[35:] == 0.0).all()


def test_activation_with_zero_ramp_is_a_step():
    r = activation(10, i0=2, ramp_n=0, hold_n=3, recovery_n=1)
    assert list(r[:6]) == [0, 0, 1, 1, 1, 0]


def test_cooling_restriction_heats_bearing_and_cooling_water_but_not_flow():
    s, ref = _series("CP-200"), _series("CP-200")
    apply_fault(s, MODELS["CP-200"], PROFILES["COOLING_RESTRICTION"], I0)
    peak = I0 + 4 * 60 + 30
    assert s.values["TT-101"][peak] > 85
    assert s.values["TT-102"][peak] > 38
    assert np.array_equal(s.values["FT-101"], ref.values["FT-101"])
    assert np.array_equal(s.values["IT-101"], ref.values["IT-101"])


def test_sensor_drift_moves_only_the_drifting_sensor():
    s, ref = _series("CP-200"), _series("CP-200")
    apply_fault(s, MODELS["CP-200"], PROFILES["SENSOR_DRIFT"], I0)
    assert s.values["TT-101"][I0 + 48 * 60] > 85
    for tag in ("TT-102", "FT-101", "PT-101", "IT-101", "VT-101"):
        assert np.array_equal(s.values[tag], ref.values[tag]), tag


def test_dry_run_collapses_flow_and_pressure():
    s = _series("CP-200")
    apply_fault(s, MODELS["CP-200"], PROFILES["DRY_RUN"], I0)
    mid = I0 + 60
    assert s.values["FT-101"][mid] < 60
    assert s.values["PT-101"][mid] < 3.0
    assert s.values["TT-101"][I0 + 60] > 85


def test_sensor_failure_flatlines_then_drops_rows():
    s = _series("CP-200")
    apply_fault(s, MODELS["CP-200"], PROFILES["SENSOR_FAILURE"], I0)
    flat = s.values["TT-101"][I0 : I0 + 180]
    assert np.all(flat == flat[0])
    assert not s.present["TT-101"][I0 + 180 : I0 + 240].any()
    assert s.present["TT-101"][I0 + 240]


def test_nuisance_spike_is_two_minutes_over_the_limit():
    s = _series("AC-75")
    apply_fault(s, MODELS["AC-75"], PROFILES["NUISANCE_SPIKE"], I0, tag="PT-301")
    v = s.values["PT-301"]
    assert (v[I0 : I0 + 2] > 8.6).all()
    assert v[I0 + 2] < 8.6


def test_fault_on_wrong_model_rejected():
    with pytest.raises(ValueError, match="CL-50"):
        apply_fault(_series("CL-50"), MODELS["CL-50"], PROFILES["DRY_RUN"], I0)


def test_every_cause_code_is_scheduled_and_assets_exist():
    assert {i.cause_code for i in INCIDENTS} == set(PROFILES)
    known = {(a.tenant_id, a.id) for a in ASSETS}
    assert all((i.tenant_id, i.asset_id) in known for i in INCIDENTS)
    assert len({i.incident_id for i in INCIDENTS}) == len(INCIDENTS)


def test_demo_incident_is_yesterday_morning_in_acme_local_time():
    inc = next(i for i in INCIDENTS if i.incident_id == "INC-01")
    end = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)  # 13:30 in Asia/Kolkata
    assert resolve_start(inc, "Asia/Kolkata", end) == datetime(2026, 10, 16, 0, 30, tzinfo=UTC)
