"""Alarm detection over generated series: limit alarms with deadband, and bad-quality alarms."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from copilot.simgen.catalog import SensorSpec
from copilot.simgen.signals import BAD

DEADBAND = 0.02  # fraction of the limit
BAD_QUALITY_MIN_MINUTES = 10
KIND_SHORT = {
    "temperature": "temp",
    "flow": "flow",
    "pressure": "pressure",
    "current": "current",
    "vibration": "vibration",
}


@dataclass(frozen=True)
class Alarm:
    tenant_id: str
    asset_id: str
    tag: str
    alarm_type: str
    severity: str
    raised_at: datetime
    cleared_at: datetime | None
    value: float | None
    limit_value: float | None
    message: str


def _runs(trigger: np.ndarray, clear: np.ndarray) -> list[tuple[int, int | None]]:
    runs: list[tuple[int, int | None]] = []
    active, begin = False, 0
    for i in range(len(trigger)):
        if not active and trigger[i]:
            active, begin = True, i
        elif active and clear[i]:
            runs.append((begin, i))
            active = False
    if active:
        runs.append((begin, None))
    return runs


def limit_alarms(
    tenant_id: str,
    asset_id: str,
    spec: SensorSpec,
    values: np.ndarray,
    present: np.ndarray,
    start: datetime,
) -> list[Alarm]:
    v = np.where(present, values, np.nan)
    out: list[Alarm] = []
    for direction, limit in (("high", spec.hi_limit), ("low", spec.lo_limit)):
        if limit is None:
            continue
        band = DEADBAND * abs(limit)
        if direction == "high":
            trigger, clear = v > limit, v < limit - band
        else:
            trigger, clear = v < limit, v > limit + band
        if not trigger.any():
            continue
        for i0, i1 in _runs(trigger, clear):
            segment = v[i0:i1] if i1 is not None else v[i0:]
            worst = np.nanmax(segment) if direction == "high" else np.nanmin(segment)
            value = round(float(v[i0]), 2)
            symbol = ">" if direction == "high" else "<"
            out.append(Alarm(
                tenant_id=tenant_id,
                asset_id=asset_id,
                tag=spec.tag,
                alarm_type=f"{direction}_{KIND_SHORT[spec.kind]}",
                severity="high" if abs(worst - limit) / abs(limit) > 0.10 else "medium",
                raised_at=start + timedelta(minutes=i0),
                cleared_at=None if i1 is None else start + timedelta(minutes=i1),
                value=value,
                limit_value=limit,
                message=(
                    f"{spec.description} {direction}: {value:.1f} {spec.unit} "
                    f"{symbol} limit {limit:g} {spec.unit}"
                ),
            ))
    return out


def quality_alarms(
    tenant_id: str,
    asset_id: str,
    spec: SensorSpec,
    present: np.ndarray,
    quality: np.ndarray,
    start: datetime,
    min_minutes: int = BAD_QUALITY_MIN_MINUTES,
) -> list[Alarm]:
    bad = ~present | (quality == BAD)
    if not bad.any():
        return []
    out: list[Alarm] = []
    for i0, i1 in _runs(bad, ~bad):
        length = (len(bad) if i1 is None else i1) - i0
        if length < min_minutes:
            continue
        out.append(Alarm(
            tenant_id=tenant_id,
            asset_id=asset_id,
            tag=spec.tag,
            alarm_type="bad_quality",
            severity="medium",
            raised_at=start + timedelta(minutes=i0),
            cleared_at=None if i1 is None else start + timedelta(minutes=i1),
            value=None,
            limit_value=None,
            message=f"{spec.description}: bad or missing signal for {length} min",
        ))
    return out
