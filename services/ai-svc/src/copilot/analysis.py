"""Deterministic analysis of sensor windows around an alarm: observations (facts) + features.

Baselines are taken at the same time of day 24 h and 48 h before the alarm so the daily cycle
cancels out; a change is significant when it exceeds a per-kind threshold.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from copilot.datasvc import AlarmInfo, AssetInfo, SensorInfo, SensorWindow

SHORT_BEFORE, SHORT_AFTER = timedelta(hours=6), timedelta(hours=2)
LONG_BEFORE, LONG_AFTER = timedelta(hours=48, minutes=30), timedelta(minutes=30)
GAP_MIN = timedelta(minutes=10)
FLAT_POINTS = 30
SHORT_ALARM_MIN = 5
ABS_THRESHOLD = {"temperature": 3.0}  # degC
REL_THRESHOLD = {"flow": 0.15, "pressure": 0.15, "current": 0.05, "vibration": 0.30}


@dataclass(frozen=True)
class Observation:
    id: str
    kind: str  # alarm | trend | sudden_change | multi_day | data_quality
    tag: str | None
    text: str
    values: dict


@dataclass(frozen=True)
class TagWindows:
    short: SensorWindow
    long: SensorWindow


@dataclass
class Analysis:
    observations: list[Observation]
    features: dict[str, bool]
    feature_refs: dict[str, list[str]]
    series: dict[str, list[dict]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Trend:
    at: float
    base24: float
    base48: float
    prev: float
    d24: float
    d48: float
    d60: float
    direction: str  # rising | falling | steady | unknown
    sudden_drop: bool
    over_days: bool
    gap_minutes: int
    flat_minutes: int
    bad_samples: int


def _mean(window: SensorWindow, t0: datetime, t1: datetime) -> float:
    vals = [p.avg for p in window.points if t0 <= p.ts < t1 and p.avg is not None]
    return float(np.mean(vals)) if vals else math.nan


def _significant(kind: str, delta: float, base: float) -> bool:
    if math.isnan(delta):
        return False
    if kind in ABS_THRESHOLD:
        return abs(delta) >= ABS_THRESHOLD[kind]
    if math.isnan(base):
        return False
    return abs(delta) >= REL_THRESHOLD[kind] * max(abs(base), 1e-6)


def _quality(window: SensorWindow) -> tuple[int, int, int]:
    pts = window.points
    step = timedelta(seconds=window.resolution_seconds)
    gap = 0
    for a, b in zip(pts, pts[1:]):
        if b.ts - a.ts > GAP_MIN:
            gap += int((b.ts - a.ts - step).total_seconds() // 60)
    longest = run = 1 if pts else 0
    for a, b in zip(pts, pts[1:]):
        run = run + 1 if a.avg is not None and a.avg == b.avg else 1
        longest = max(longest, run)
    flat = int(longest * window.resolution_seconds / 60) if longest >= FLAT_POINTS else 0
    return gap, flat, sum(p.bad for p in pts)


def _trend(sensor: SensorInfo, w: TagWindows, t: datetime) -> _Trend:
    m = timedelta(minutes=1)
    at = _mean(w.short, t - 10 * m, t + 20 * m)
    base24 = _mean(w.long, t - timedelta(hours=24) - 30 * m, t - timedelta(hours=24) + 30 * m)
    base48 = _mean(w.long, t - timedelta(hours=48) - 30 * m, t - timedelta(hours=48) + 30 * m)
    prev = _mean(w.short, t - 70 * m, t - 40 * m)
    d24, d48, d60 = at - base24, at - base48, at - prev
    sig24, sig48 = _significant(sensor.kind, d24, base24), _significant(sensor.kind, d48, base48)
    deltas = [d for d, s in ((d24, sig24), (d48, sig48)) if s]
    if math.isnan(at):
        direction = "unknown"
    elif deltas:
        direction = "rising" if max(deltas, key=abs) > 0 else "falling"
    else:
        direction = "steady"
    sudden = (not math.isnan(prev) and _significant(sensor.kind, d60, prev)
              and d60 <= -0.4 * abs(prev))
    over_days = sig48 and d48 > 0 and _significant(sensor.kind, d48 - d24, base48)
    gap, flat, bad = _quality(w.short)
    return _Trend(at, base24, base48, prev, d24, d48, d60, direction, sudden, over_days, gap, flat, bad)


def _r(x: float) -> float:
    return round(float(x), 1)


def analyze(asset: AssetInfo, alarm: AlarmInfo, windows: dict[str, TagWindows],
            roles: dict[str, str], timezone: str) -> Analysis:
    zone = ZoneInfo(timezone)
    t = alarm.raised_at
    alarmed = asset.sensor(alarm.tag)
    obs: list[Observation] = []
    refs: dict[str, list[str]] = {}

    def add(kind: str, tag: str | None, text: str, values: dict, *features: str) -> str:
        oid = f"O{len(obs) + 1}"
        obs.append(Observation(oid, kind, tag, text, values))
        for f in features:
            refs.setdefault(f, []).append(oid)
        return oid

    local = t.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")
    duration = None if alarm.cleared_at is None else int((alarm.cleared_at - t).total_seconds() // 60)
    after = [p.max for p in windows[alarm.tag].short.points
             if t <= p.ts < t + SHORT_AFTER and p.max is not None]
    peak = max(after) if after else math.nan
    text = f"{alarm.tag} ({alarmed.description}) raised {alarm.alarm_type} at {local}: {alarm.message}."
    text += (f" It cleared after {duration} min." if duration is not None
             else " It was still active at the end of the available data.")
    if not math.isnan(peak):
        text += f" Peak {_r(peak)} {alarmed.unit} within 2 h after the alarm."
    short = duration is not None and duration <= SHORT_ALARM_MIN
    add("alarm", alarm.tag, text, {"value": alarm.value, "limit": alarm.limit_value,
        "peak": None if math.isnan(peak) else _r(peak), "duration_min": duration},
        *(["alarm.short"] if short else []))

    features: dict[str, bool] = {"alarm.short": short}
    trends: dict[str, _Trend] = {}
    order = [alarm.tag] + sorted(tag for tag in windows if tag != alarm.tag)
    for tag in order:
        sensor = asset.sensor(tag)
        tr = trends[tag] = _trend(sensor, windows[tag], t)
        names = [roles.get(tag, tag)] + (["alarmed"] if tag == alarm.tag else [])
        bad_data = (tr.direction == "unknown" or tr.gap_minutes >= 10 or tr.flat_minutes > 0
                    or tr.bad_samples > 0)
        flags = {"rising": tr.direction == "rising", "falling": tr.direction == "falling",
                 "flat": tr.direction == "steady", "no_trend": tr.direction == "steady",
                 "sudden_drop": tr.sudden_drop, "over_days": tr.over_days, "bad_data": bad_data}
        for name in names:
            for flag, value in flags.items():
                features[f"{name}.{flag}"] = value
        trend_features = [f"{n}.{f}" for n in names for f in ("rising", "falling", "flat", "no_trend")
                          if flags[f]]
        u = sensor.unit
        if tr.direction == "unknown":
            add("trend", tag, f"{tag} ({sensor.description}) has no readings around the alarm time.",
                {"delta": None}, *[f"{n}.bad_data" for n in names])
        elif tr.direction in ("rising", "falling"):
            use48 = not math.isnan(tr.d48) and abs(tr.d48) > abs(tr.d24 if not math.isnan(tr.d24) else 0)
            d, base, hours = (tr.d48, tr.base48, 48) if use48 else (tr.d24, tr.base24, 24)
            verb = "rose" if d > 0 else "fell"
            add("trend", tag, f"{tag} ({sensor.description}) {verb} {_r(abs(d))} {u} versus {hours} h "
                f"earlier ({_r(base)} -> {_r(tr.at)} {u}).",
                {"delta": _r(d), "from": _r(base), "to": _r(tr.at), "hours": hours}, *trend_features)
        else:
            d = 0.0 if math.isnan(tr.d24) else tr.d24
            add("trend", tag, f"{tag} ({sensor.description}) was steady around the alarm ({_r(tr.at)} {u}; "
                f"change {_r(d):+} {u} versus 24 h earlier).", {"delta": _r(d), "to": _r(tr.at)},
                *trend_features)
        if tr.sudden_drop:
            pct = 100 * -tr.d60 / abs(tr.prev)
            add("sudden_change", tag, f"{tag} dropped {pct:.0f}% within the hour before the alarm "
                f"({_r(tr.prev)} -> {_r(tr.at)} {u}).",
                {"percent": round(pct), "from": _r(tr.prev), "to": _r(tr.at)},
                *[f"{n}.sudden_drop" for n in names])
        if tr.over_days:
            add("multi_day", tag, f"{tag} has been rising for more than a day ({_r(tr.base48)} {u} 48 h "
                f"earlier, {_r(tr.base24)} {u} 24 h earlier, {_r(tr.at)} {u} at the alarm).",
                {"h48": _r(tr.base48), "h24": _r(tr.base24), "at": _r(tr.at)},
                *[f"{n}.over_days" for n in names])
        if bad_data and tr.direction != "unknown":
            add("data_quality", tag, f"{tag} data quality around the alarm: {tr.gap_minutes} min missing, "
                f"flat (identical readings) for {tr.flat_minutes} min, {tr.bad_samples} bad-quality samples.",
                {"gap_minutes": tr.gap_minutes, "flat_minutes": tr.flat_minutes,
                 "bad_samples": tr.bad_samples},
                *[f"{n}.bad_data" for n in names])

    others = [trends[tag] for tag in order[1:]]
    features["related.all_flat"] = bool(others) and all(tr.direction == "steady" for tr in others)
    if features["related.all_flat"]:
        refs["related.all_flat"] = [o.id for o in obs if o.kind == "trend" and o.tag != alarm.tag]
    series = {
        tag: [{"ts": p.ts.isoformat(), "avg": p.avg, "min": p.min, "max": p.max}
              for p in windows[tag].short.points]
        for tag in order
    }
    return Analysis(obs, features, refs, series)
