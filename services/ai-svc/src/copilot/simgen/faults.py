"""Fault profiles: how each root cause changes an asset's sensor series."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

import numpy as np

from copilot.simgen.catalog import AssetModel
from copilot.simgen.signals import AssetSeries

RECOVERY_MINUTES = 30

# (series, model, i0, ramp_n, hold_n, tag) -> None, mutates series in place
FaultFn = Callable[[AssetSeries, AssetModel, int, int, int, str | None], None]


def activation(
    n: int, i0: int, ramp_n: int, hold_n: int, recovery_n: int = RECOVERY_MINUTES
) -> np.ndarray:
    """0 before i0, linear rise to 1 over ramp_n, 1 for hold_n, linear fall over recovery_n."""
    idx = np.arange(n)
    r = np.zeros(n)
    i_peak = i0 + ramp_n
    i_end = i_peak + hold_n
    rising = (idx >= i0) & (idx < i_peak)
    r[rising] = (idx[rising] - i0 + 1) / max(ramp_n, 1)
    r[(idx >= i_peak) & (idx < i_end)] = 1.0
    falling = (idx >= i_end) & (idx < i_end + recovery_n)
    r[falling] = 1.0 - (idx[falling] - i_end + 1) / recovery_n
    return r


def _r(s: AssetSeries, i0: int, ramp_n: int, hold_n: int) -> np.ndarray:
    return activation(len(next(iter(s.values.values()))), i0, ramp_n, hold_n)


def _cooling_restriction(s, model, i0, ramp_n, hold_n, tag):
    r = _r(s, i0, ramp_n, hold_n)
    s.values["TT-102"] += 14.0 * r
    s.values["TT-101"] += 30.0 * r


def _bearing_wear(s, model, i0, ramp_n, hold_n, tag):
    r = _r(s, i0, ramp_n, hold_n)
    s.values["VT-101"] += 6.5 * r
    s.values["TT-101"] += 26.0 * r
    s.values["IT-101"] += 3.0 * r


def _sensor_drift(s, model, i0, ramp_n, hold_n, tag):
    s.values["TT-101"] += 26.0 * _r(s, i0, ramp_n, hold_n)


def _dry_run(s, model, i0, ramp_n, hold_n, tag):
    r = _r(s, i0, ramp_n, hold_n)
    r_temp = _r(s, i0, 40, ramp_n + hold_n - 40)  # bearing heats more slowly than flow collapses
    s.values["FT-101"] *= 1.0 - 0.95 * r
    s.values["PT-101"] *= 1.0 - 0.8 * r
    s.values["IT-101"] *= 1.0 - 0.35 * r
    s.values["TT-101"] += 28.0 * r_temp


def _cavitation(s, model, i0, ramp_n, hold_n, tag):
    r = _r(s, i0, ramp_n, hold_n)
    s.values["FT-101"] *= 1.0 - 0.55 * r
    s.values["PT-101"] -= 2.0 * r
    s.values["VT-101"] += 5.5 * r


def _sensor_failure(s, model, i0, ramp_n, hold_n, tag):
    v = s.values["TT-101"]
    flat_end = i0 + ramp_n
    v[i0:flat_end] = v[i0]
    s.present["TT-101"][flat_end : flat_end + hold_n] = False


def _hx_fouling(s, model, i0, ramp_n, hold_n, tag):
    r = _r(s, i0, ramp_n, hold_n)
    s.values["TT-201"] += 12.0 * r
    s.values["TT-202"] += 5.0 * r


def _cooler_fan_failure(s, model, i0, ramp_n, hold_n, tag):
    s.values["TT-301"] += 22.0 * _r(s, i0, ramp_n, hold_n)


def _seal_leak(s, model, i0, ramp_n, hold_n, tag):
    r = _r(s, i0, ramp_n, hold_n)
    s.values["PT-101"] -= 4.0 * r
    s.values["FT-101"] *= 1.0 - 0.08 * r
    s.values["IT-101"] *= 1.0 - 0.03 * r


def _circulation_pump_wear(s, model, i0, ramp_n, hold_n, tag):
    r = _r(s, i0, ramp_n, hold_n)
    s.values["FT-201"] *= 1.0 - 0.4 * r
    s.values["PT-201"] -= 0.6 * r


def _nuisance_spike(s, model, i0, ramp_n, hold_n, tag):
    spec = model.sensor(tag)
    level = spec.hi_limit * 1.08 if spec.hi_limit is not None else spec.lo_limit * 0.9
    s.values[tag][i0 : i0 + hold_n] = level


@dataclass(frozen=True)
class FaultProfile:
    cause_code: str
    asset_model: str | None  # None = any model
    ramp: timedelta
    hold: timedelta
    primary_tag: str | None  # None = taken from the incident
    fn: FaultFn


_H = timedelta(hours=1)
_M = timedelta(minutes=1)

PROFILES: dict[str, FaultProfile] = {p.cause_code: p for p in (
    FaultProfile("COOLING_RESTRICTION", "CP-200", 4 * _H, 3 * _H, "TT-101", _cooling_restriction),
    FaultProfile("BEARING_WEAR", "CP-200", 72 * _H, 6 * _H, "VT-101", _bearing_wear),
    FaultProfile("SENSOR_DRIFT", "CP-200", 48 * _H, 4 * _H, "TT-101", _sensor_drift),
    FaultProfile("DRY_RUN", "CP-200", 10 * _M, 90 * _M, "FT-101", _dry_run),
    FaultProfile("CAVITATION", "CP-200", 2 * _H, 6 * _H, "VT-101", _cavitation),
    FaultProfile("SENSOR_FAILURE", "CP-200", 3 * _H, 1 * _H, "TT-101", _sensor_failure),
    FaultProfile("HX_FOULING", "CL-50", 48 * _H, 6 * _H, "TT-201", _hx_fouling),
    FaultProfile("COOLER_FAN_FAILURE", "AC-75", 3 * _H, 4 * _H, "TT-301", _cooler_fan_failure),
    FaultProfile("NUISANCE_SPIKE", None, timedelta(0), 2 * _M, None, _nuisance_spike),
    # no signature describes these two (holdout "novel fault" cases)
    FaultProfile("SEAL_LEAK", "CP-200", 6 * _H, 2 * _H, "PT-101", _seal_leak),
    FaultProfile("CIRCULATION_PUMP_WEAR", "CL-50", 12 * _H, 4 * _H, "FT-201", _circulation_pump_wear),
)}


def _minutes(td: timedelta) -> int:
    return int(td.total_seconds() // 60)


def apply_fault(
    series: AssetSeries, model: AssetModel, profile: FaultProfile, i0: int, tag: str | None = None
) -> None:
    if profile.asset_model is not None and profile.asset_model != model.code:
        raise ValueError(f"{profile.cause_code} applies to {profile.asset_model}, not {model.code}")
    if profile.primary_tag is None and tag is None:
        raise ValueError(f"{profile.cause_code} needs a tag")
    profile.fn(series, model, i0, _minutes(profile.ramp), _minutes(profile.hold), tag)
